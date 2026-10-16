import Foundation
import Network
import Observation

/// Online matches: my matches and open matches from the server, saved on the phone with
/// the exact game version each one plays, so any match can be opened and played offline.
/// Moves are shown at once and queued; the queue is sent in order with `expected_move_count`.
@MainActor @Observable
final class MatchesModel {
    /// A move made on the phone that the server hasn't accepted yet.
    struct PendingMove: Codable, Identifiable {
        var id = UUID()
        let matchId: String
        let newState: JSONValue
        let nextTurn: [Int]?
        let expectedMoveCount: Int
        /// Made for a computer seat. If another player's app sent that move first, ours is
        /// stale; the README says to just ignore it, so no message is shown.
        let forComputer: Bool
    }

    private let app: AppModel
    /// Matches I own or play in (newest first), with my queued moves applied.
    private(set) var myMatches: [Match] = GameCache.load("matches", as: [Match].self) ?? []
    /// Matches I could join (from `GET /matches/open`), and matches opened by link.
    private(set) var otherMatches: [Match] = []
    private(set) var pendingMoves: [PendingMove] = GameCache.load("pending-moves", as: [PendingMove].self) ?? []
    /// The exact game version each match plays, by "gameId@version".
    private var versions: [String: Game] = GameCache.load("game-versions", as: [String: Game].self) ?? [:]
    private var names: [String: String] = GameCache.load("user-names", as: [String: String].self) ?? [:]
    /// A message to show, e.g. "your offline move was rejected".
    var notice: String?
    var isLoading = false
    private var isUploading = false
    private let network = NWPathMonitor()

    init(app: AppModel) {
        self.app = app
        // Send queued moves as soon as the network is back.
        network.pathUpdateHandler = { [weak self] path in
            guard path.status == .satisfied else { return }
            Task { @MainActor in
                guard let self, !self.pendingMoves.isEmpty else { return }
                await self.uploadMoves()
            }
        }
        network.start(queue: .main)
    }

    private var me: String? { app.account?.id }

    func match(id: String) -> Match? {
        myMatches.first { $0.id == id } ?? otherMatches.first { $0.id == id }
    }

    var joinableMatches: [Match] {
        otherMatches.filter { m in m.seat(of: me) == nil && !myMatches.contains { $0.id == m.id } }
    }

    func game(for match: Match) -> Game? {
        versions["\(match.gameId)@\(match.gameVersion)"]
            ?? app.games.first { $0.id == match.gameId && $0.version == match.gameVersion }
    }

    func name(of seat: Match.Seat) -> String {
        guard let userId = seat.userId else { return "Computer \(seat.playerIndex + 1)" }
        if userId == me { return app.account?.displayName ?? "You" }
        return names[userId] ?? "Player \(seat.playerIndex + 1)"
    }

    func hasPendingMoves(_ match: Match) -> Bool { pendingMoves.contains { $0.matchId == match.id } }

    // MARK: Whose turn (like `actingFor` in static/portal.js)

    func mySeat(_ match: Match) -> Int? { match.seat(of: me)?.playerIndex }

    /// The seat my game should move for: mine on my turn, or a computer's on a computer's
    /// turn (any human player may send the computer's move). nil = wait.
    func actingSeat(_ match: Match) -> Int? {
        guard match.isOngoing, let turn = match.turnOfPlayerIndices, let mine = mySeat(match) else { return nil }
        if turn.contains(mine) { return mine }
        return turn.first { match.players.indices.contains($0) && match.players[$0].kind == "computer" }
    }

    func isMyTurn(_ match: Match) -> Bool {
        guard let mine = mySeat(match) else { return false }
        return match.isOngoing && (match.turnOfPlayerIndices ?? []).contains(mine)
    }

    /// The `state_changed` message for the game (see "Game API" in the root README).
    func stateChanged(_ match: Match) -> StateChanged {
        StateChanged(
            state: match.state,
            players: match.players.map {
                LocalMatch.Player(playerIndex: $0.playerIndex, kind: $0.kind == "computer" ? .computer : .human, name: name(of: $0))
            },
            turnOfPlayerIndices: match.turnOfPlayerIndices,
            turnOfPlayerIndex: match.turnOfPlayerIndices?.first,
            status: match.isOver ? "over" : "ongoing",
            endReason: match.endReason,
            moveCount: match.moveCount,
            myPlayerIndex: mySeat(match),
            actingForPlayerIndex: actingSeat(match))
    }

    // MARK: Loading

    /// Sends queued moves, then reloads my matches and the open ones. Also downloads the game
    /// version and player names of every match, so they're on the phone for offline use.
    func refreshList() async {
        guard let account = app.account else { return }
        isLoading = true
        defer { isLoading = false }
        await uploadMoves()
        do {
            let mine = try await API.myMatches(as: account)
            // Keep my local copy of matches whose moves are still queued.
            myMatches = mine.map { m in hasPendingMoves(m) ? (match(id: m.id) ?? m) : m }
            otherMatches = (try? await API.openMatches()) ?? otherMatches
            app.isOffline = false
            save()
            await downloadDetails(for: myMatches + otherMatches)
        } catch {
            app.isOffline = error is URLError
            if !app.isOffline { notice = error.localizedDescription }
        }
    }

    /// Reloads one match (the match screen calls this every 2 seconds).
    func refresh(matchId: String) async {
        await uploadMoves()
        if pendingMoves.contains(where: { $0.matchId == matchId }) { return } // offline: keep my copy
        do {
            store(try await API.match(id: matchId))
            app.isOffline = false
            if let m = match(id: matchId) { await downloadDetails(for: [m]) }
        } catch {
            app.isOffline = error is URLError
        }
    }

    private func downloadDetails(for matches: [Match]) async {
        for m in matches where game(for: m) == nil {
            if let g = try? await API.gameVersion(id: m.gameId, version: m.gameVersion) {
                versions["\(m.gameId)@\(m.gameVersion)"] = g
            }
        }
        for userId in Set(matches.flatMap { $0.players.compactMap(\.userId) }) where names[userId] == nil && userId != me {
            if let n = try? await API.displayName(userId: userId) { names[userId] = n }
        }
        GameCache.save(versions, as: "game-versions")
        GameCache.save(names, as: "user-names")
    }

    /// Puts a match from the server into the right list.
    private func store(_ m: Match) {
        if m.seat(of: me) != nil || m.ownerUserId == me {
            if let i = myMatches.firstIndex(where: { $0.id == m.id }) { myMatches[i] = m } else { myMatches.insert(m, at: 0) }
            otherMatches.removeAll { $0.id == m.id }
        } else if let i = otherMatches.firstIndex(where: { $0.id == m.id }) {
            otherMatches[i] = m
        } else {
            otherMatches.append(m)
        }
        save()
    }

    private func save() {
        GameCache.save(myMatches, as: "matches")
        GameCache.save(pendingMoves, as: "pending-moves")
    }

    /// On log out: forget my matches and queued moves (saved game versions stay).
    func forgetAccount() {
        myMatches = []
        otherMatches = []
        pendingMoves = []
        notice = nil
        save()
    }

    // MARK: Creating, joining, starting (need the network)

    func create(game: Game, computers: Int) async throws -> Match {
        let m = try await API.createMatch(gameId: game.id, computers: computers, as: try account())
        versions["\(game.id)@\(m.gameVersion)"] = versions["\(game.id)@\(m.gameVersion)"] ?? (game.version == m.gameVersion ? game : nil)
        store(m)
        await downloadDetails(for: [m])
        return m
    }

    func join(matchId: String) async throws -> Match {
        let m = try await API.joinMatch(id: matchId, as: try account())
        store(m)
        await downloadDetails(for: [m])
        return m
    }

    /// Opens a match from a shared link (or a bare id) without joining it yet.
    func open(linkOrId: String) async throws -> Match {
        let text = linkOrId.trimmingCharacters(in: .whitespacesAndNewlines)
        let id = text.components(separatedBy: "match=").last ?? text
        let m = try await API.match(id: id)
        store(m)
        await downloadDetails(for: [m])
        return m
    }

    func start(matchId: String) async throws {
        store(try await API.startMatch(id: matchId, as: try account()))
    }

    func setComputers(matchId: String, _ n: Int) async throws {
        store(try await API.setComputers(matchId: matchId, n, as: try account()))
    }

    private func account() throws -> Account {
        guard let account = app.account else { throw URLError(.userAuthenticationRequired) }
        return account
    }

    // MARK: Moves (work offline)

    /// A `make_move` from the game: shown at once, queued, then sent.
    func makeMove(matchId: String, newState: JSONValue, nextTurn: [Int]?) {
        guard var m = match(id: matchId), let seat = actingSeat(m) else { return }
        pendingMoves.append(PendingMove(matchId: matchId, newState: newState, nextTurn: nextTurn,
                                        expectedMoveCount: m.moveCount, forComputer: m.players[seat].kind == "computer"))
        m.state = newState
        m.turnOfPlayerIndices = nextTurn
        m.moveCount += 1
        if nextTurn == nil { m.status = "over"; m.endReason = "finished" }
        store(m)
        Task { await uploadMoves() }
    }

    /// Sends queued moves in order. Stops at a network error (tries again later). If the
    /// server rejects a move (stale, or the match changed), that match's queued moves are
    /// dropped, a message is shown (unless it was a computer's move), and the match reloads.
    func uploadMoves() async {
        guard let account = app.account, !isUploading else { return }
        isUploading = true
        defer { isUploading = false }
        while let move = pendingMoves.first {
            do {
                let saved = try await API.makeMove(matchId: move.matchId, newState: move.newState, nextTurn: move.nextTurn,
                                                   expectedMoveCount: move.expectedMoveCount, as: account)
                pendingMoves.removeFirst()
                if !pendingMoves.contains(where: { $0.matchId == move.matchId }) { store(saved) }
                app.isOffline = false
            } catch let error as URLError {
                _ = error
                app.isOffline = true
                return
            } catch APIError.server(let status, _) where status >= 500 || status == 401 {
                return // may work later
            } catch {
                pendingMoves.removeAll { $0.matchId == move.matchId }
                let stale = error.localizedDescription.contains("expected")
                if !(stale && move.forComputer) {
                    let gameName = match(id: move.matchId).flatMap { game(for: $0)?.name } ?? "a match"
                    notice = stale
                        ? "Your move in \(gameName) was too late: the match changed meanwhile. It was reloaded."
                        : "Your move in \(gameName) wasn't accepted (\(error.localizedDescription)). It was reloaded."
                }
                if let fresh = try? await API.match(id: move.matchId) { store(fresh) }
            }
            save()
        }
    }
}
