import Foundation
import Network
import Observation

/// App state shared by all screens: who is logged in, the games list, and the queue of
/// game edits that haven't reached the server yet.
@MainActor @Observable
final class AppModel {
    var account: Account? = Keychain.load()
    /// The games list as shown: the server's list plus the edits still waiting to upload.
    var games: [Game] = []
    /// Edits made on the phone, oldest first, uploaded in order.
    private(set) var pendingEdits: [PendingEdit] = GameCache.loadQueue()
    /// When the shown list was last downloaded (nil = never).
    var gamesSavedAt: Date?
    /// True when the last request failed for lack of network, so we're showing saved data.
    private(set) var isOffline = false
    var isLoading = false
    var errorMessage: String?

    private var isSyncing = false
    /// Offline-created games get a temporary id; this maps it to the server's id once uploaded.
    private var uploadedIds: [String: String] = [:]
    private let network = NWPathMonitor()

    init() {
        #if DEBUG
        // For UI tests: `-resetLocalData` forgets this server's account, games and queue.
        if ProcessInfo.processInfo.arguments.contains("-resetLocalData") {
            Keychain.delete()
            GameCache.reset()
            account = nil
            pendingEdits = []
        }
        // For UI tests: `-loginAs <id> <password>` uses an account the test created through
        // the REST API (kept in memory only, never saved in the Keychain).
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "-loginAs"), i + 2 < args.count {
            account = Account(id: args[i + 1], password: args[i + 2], displayName: "UI Test")
        }
        // For screenshots: `-screenshotMode` shows the Games screen with a placeholder
        // account that is never saved or sent to the server.
        if account == nil, ProcessInfo.processInfo.arguments.contains("-screenshotMode") {
            account = Account(id: "screenshot", password: "", displayName: "Marcelle Ribeiro")
        }
        #endif
        if let cached = GameCache.load() {
            games = cached.games
            gamesSavedAt = cached.savedAt
        }
        // When the network comes back (e.g. airplane mode off), upload queued edits.
        network.pathUpdateHandler = { [weak self] path in
            guard path.status == .satisfied else { return }
            Task { @MainActor in
                guard let self, self.isOffline || !self.pendingEdits.isEmpty else { return }
                await self.refreshGames()
            }
        }
        network.start(queue: .main)
    }

    /// Changes the offline flag only when it really changes, so screens that show it aren't
    /// redrawn on every successful request.
    func setOffline(_ offline: Bool) {
        if isOffline != offline { isOffline = offline }
    }

    func isMine(_ game: Game) -> Bool { game.ownerUserId == account?.id }
    func isPending(_ game: Game) -> Bool { pendingEdits.contains { $0.gameId == game.id } }

    /// The latest version of a game shown on screen (after edits, or after an offline-created
    /// game got its server id). nil if it was deleted.
    func current(_ game: Game) -> Game? {
        let id = uploadedIds[game.id] ?? game.id
        return games.first { $0.id == id }
    }

    /// Sends the ID token from Google's sign-in sheet to buildplay.fun and keeps the
    /// returned account in the Keychain.
    func logInWithGoogle(idToken: String) async {
        await logIn { try await API.logInWithGoogle(idToken: idToken) }
    }

    /// Debug builds only (launch argument `-guestLogin`), so UI tests can log in.
    func logInAsGuest(displayName: String) async {
        await logIn { try await API.createGuest(displayName: displayName) }
    }

    private func logIn(_ request: () async throws -> Account) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let newAccount = try await request()
            try Keychain.save(newAccount)
            account = newAccount
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Forgets the account and everything that belongs to it (queued edits; matches are
    /// forgotten by MatchesModel). The class games list stays, so it still works offline.
    func logOut() {
        Keychain.delete()
        games.removeAll { isPending($0) }
        pendingEdits = []
        GameCache.saveQueue([])
        GameCache.save(games)
        uploadedIds = [:]
        errorMessage = nil
        account = nil
    }

    /// Uploads queued edits, then downloads the games list. On failure (e.g. airplane mode)
    /// the saved list stays on screen.
    func refreshGames() async {
        isLoading = true
        defer { isLoading = false }
        await uploadPendingEdits()
        do {
            let fresh = Self.merging(mine: try await fetchMyGames(), into: try await API.fetchGames())
            games = Self.applying(pendingEdits, to: fresh, owner: account?.id ?? "")
            gamesSavedAt = .now
            setOffline(false)
            GameCache.save(games)
        } catch {
            setOffline(Self.isNetworkError(error))
            errorMessage = error.localizedDescription
        }
    }

    // MARK: Editing (works offline: shown at once, uploaded when possible)

    func createGame(_ fields: GameFields) {
        let edit = PendingEdit(kind: .create, gameId: "local-\(UUID().uuidString)", fields: fields)
        pendingEdits.append(edit)
        games = Self.applying([edit], to: games, owner: account?.id ?? "")
        saveAndUpload()
    }

    func updateGame(_ game: Game, _ fields: GameFields) {
        if let i = pendingEdits.firstIndex(where: { $0.gameId == game.id && $0.kind != .delete }) {
            pendingEdits[i].fields = fields   // not uploaded yet: just change what will be sent
        } else {
            pendingEdits.append(PendingEdit(kind: .update, gameId: game.id, fields: fields))
        }
        games = games.map { $0.id == game.id ? $0.with(fields) : $0 }
        saveAndUpload()
    }

    func deleteGame(_ game: Game) {
        let createdOffline = pendingEdits.contains { $0.gameId == game.id && $0.kind == .create }
        pendingEdits.removeAll { $0.gameId == game.id }
        // A game the server never saw just disappears; otherwise the server must delete it.
        if !createdOffline { pendingEdits.append(PendingEdit(kind: .delete, gameId: game.id)) }
        games.removeAll { $0.id == game.id }
        saveAndUpload()
    }

    private func saveAndUpload() {
        GameCache.saveQueue(pendingEdits)
        GameCache.save(games)
        Task { await uploadPendingEdits() }
    }

    // MARK: Uploading the queue

    /// Sends queued edits in order. Stops at a network error (tries again later); drops an
    /// edit the server refuses for good (e.g. the game was deleted), so the queue can't get stuck.
    func uploadPendingEdits() async {
        guard let account, !isSyncing else { return }
        isSyncing = true
        defer { isSyncing = false }
        while let edit = pendingEdits.first {
            do {
                switch edit.kind {
                case .create:
                    let saved = try await API.createGame(edit.fields!, as: account)
                    replaceId(edit.gameId, with: saved.id)
                    replaceGame(saved)
                case .update:
                    replaceGame(try await API.updateGame(id: edit.gameId, edit.fields!, as: account))
                case .delete:
                    try await API.deleteGame(id: edit.gameId, as: account)
                }
                setOffline(false)
            } catch where Self.isNetworkError(error) || Self.isServerTrouble(error) {
                setOffline(Self.isNetworkError(error))
                return
            } catch {
                errorMessage = "Couldn't upload a change (\(edit.kind.rawValue)): \(error.localizedDescription)"
            }
            pendingEdits.removeAll { $0.id == edit.id }
            GameCache.saveQueue(pendingEdits)
            GameCache.save(games)
        }
    }

    /// An offline-created game was uploaded: use the server's id from now on.
    private func replaceId(_ localId: String, with serverId: String) {
        uploadedIds[localId] = serverId
        for i in pendingEdits.indices where pendingEdits[i].gameId == localId { pendingEdits[i].gameId = serverId }
        // A refresh during the upload may already have listed the server's copy.
        if games.contains(where: { $0.id == serverId }) { games.removeAll { $0.id == localId }; return }
        games = games.map { $0.id == localId ? Game(id: serverId, version: $0.version, ownerUserId: $0.ownerUserId,
            name: $0.name, description: $0.description, allowedPlayerCounts: $0.allowedPlayerCounts,
            allowsLeaveMidMatch: $0.allowsLeaveMidMatch, allowsJoinMidMatch: $0.allowsJoinMidMatch, code: $0.code,
            published: $0.published) : $0 }
    }

    /// Shows the server's copy, unless newer edits for it are still queued.
    private func replaceGame(_ saved: Game) {
        guard !pendingEdits.dropFirst().contains(where: { $0.gameId == saved.id }) else { return }
        games = games.map { $0.id == saved.id ? saved : $0 }
    }

    /// My games from `GET /builder/games` (they include unpublished ones). Empty without a real
    /// login (the screenshot placeholder); a network failure is thrown like for `GET /games`.
    private func fetchMyGames() async throws -> [Game] {
        guard let account, !account.password.isEmpty else { return [] }
        return try await API.fetchMyGames(as: account)
    }

    /// The class games (published) plus my own games, mine first; my copy wins when both
    /// lists have the same game.
    private static func merging(mine: [Game], into published: [Game]) -> [Game] {
        let myIds = Set(mine.map(\.id))
        return mine + published.filter { !myIds.contains($0.id) }
    }

    /// The list with queued edits applied on top (used after downloading the server's list).
    private static func applying(_ edits: [PendingEdit], to games: [Game], owner: String) -> [Game] {
        var result = games
        for edit in edits {
            switch edit.kind {
            case .create:
                let f = edit.fields!
                result.insert(Game(id: edit.gameId, version: 0, ownerUserId: owner, name: f.name,
                                   description: f.description, allowedPlayerCounts: f.allowedPlayerCounts,
                                   allowsLeaveMidMatch: false, allowsJoinMidMatch: false, code: f.code), at: 0)
            case .update:
                result = result.map { $0.id == edit.gameId ? $0.with(edit.fields!) : $0 }
            case .delete:
                result.removeAll { $0.id == edit.gameId }
            }
        }
        return result
    }

    private static func isNetworkError(_ error: Error) -> Bool { error is URLError }

    /// 5xx errors and bad credentials (401) may get better later, so the edit is kept.
    private static func isServerTrouble(_ error: Error) -> Bool {
        if case APIError.server(let status, _) = error { return status >= 500 || status == 401 }
        return false
    }
}
