import SwiftUI
import UIKit
import CoreImage.CIFilterBuiltins

@main struct GamePortalApp: App {
    @StateObject private var store = PortalStore()
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(store).tint(Palette.teal)
                .preferredColorScheme(.light)
                .alert("Storage needs attention", isPresented: Binding(get: { store.storageError != nil }, set: { if !$0 { store.storageError = nil } })) {
                    Button("OK") { store.storageError = nil }
                } message: { Text(store.storageError ?? "") }
        }
    }
}

enum Palette {
    static let ink = Color(red: 0.07, green: 0.16, blue: 0.22)
    static let teal = Color(red: 0.02, green: 0.43, blue: 0.43)
    static let paper = Color(red: 0.96, green: 0.96, blue: 0.93)
    static let orange = Color(red: 0.84, green: 0.30, blue: 0.17)
}

struct PrimaryButton: View {
    let title: String
    var action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title).font(.headline).frame(maxWidth: .infinity).padding(17)
        }.buttonStyle(.plain).foregroundStyle(.white).background(Palette.teal, in: RoundedRectangle(cornerRadius: 18))
    }
}

struct RootView: View {
    @EnvironmentObject var store: PortalStore
    @StateObject private var online = OnlineStore()
    @State private var tab = 0
    @State private var game = ""
    var body: some View {
        if store.guest.isEmpty { WelcomeView() }
        else {
            TabView(selection: $tab) {
                NavigationStack { DiscoverView { id in
                    game = id; online.selected = nil; online.code = ""; tab = 1
                } }.tabItem { Label("Discover", systemImage: "square.grid.2x2") }.tag(0)
                NavigationStack {
                    if tab == 1 { OnlinePortalView(game: $game) }
                }.tabItem { Label("Online Play", systemImage: "person.2.fill") }.tag(1)
                NavigationStack { MatchesView() }
                    .tabItem { Label("Local Matches", systemImage: "iphone") }.tag(2)
            }.environmentObject(online)
                .task { await online.refresh() }
        }
    }
}

struct WelcomeView: View {
    @EnvironmentObject var store: PortalStore
    @State private var name = ""
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                Label("GAMEPORTAL", systemImage: "circle.hexagongrid.fill").font(.caption.bold()).tracking(3)
                Spacer(minLength: 35)
                HStack(spacing: 16) {
                    Text("×").foregroundStyle(Palette.teal)
                    Text("○").foregroundStyle(Palette.orange)
                }.font(.system(size: 100, weight: .medium, design: .rounded))
                Text("Good games.\nSame table.").font(.system(size: 46, weight: .bold, design: .rounded))
                Text("A little friendly competition, wherever you are. Pick a game and pass the phone.")
                    .font(.title3).foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 12) {
                    Text("WHAT SHOULD WE CALL YOU?").font(.caption.bold()).tracking(1)
                    TextField("Your name", text: $name).textContentType(.nickname).padding().background(.white, in: RoundedRectangle(cornerRadius: 14))
                        .accessibilityIdentifier("guestName")
                    PrimaryButton(title: "Play as guest  →") {
                        store.guest = name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Player 1" : String(name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(30))
                        store.save()
                    }
                }
                Label("Local play · No account needed", systemImage: "iphone").font(.footnote).foregroundStyle(.secondary)
            }.padding(28).foregroundStyle(Palette.ink)
        }.background(Palette.paper)
    }
}

struct DiscoverView: View {
    @EnvironmentObject var store: PortalStore
    @EnvironmentObject private var online: OnlineStore
    var play: (String) -> Void
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("YOUR POCKET GAME ROOM").font(.caption.bold()).tracking(2).foregroundStyle(Palette.teal)
                Text("Your move, \(store.guest).").font(.system(size: 34, weight: .bold, design: .rounded))
                Text("Swipe to find your next game.").foregroundStyle(.secondary)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 16) {
                        VStack(alignment: .leading, spacing: 18) {
                            Text("ON THIS PHONE").font(.caption.bold()).tracking(2)
                            Text("× ○").font(.system(size: 70, weight: .bold, design: .rounded)).foregroundStyle(.mint)
                            Text("Tic-Tac-Toe").font(.title.bold())
                            Text("Pass the phone. Play anywhere.").font(.subheadline)
                            NavigationLink { SetupView() } label: { Label("Pass and play", systemImage: "iphone").font(.headline).padding(12).background(.white.opacity(0.15), in: RoundedRectangle(cornerRadius: 12)) }
                        }.frame(width: 250, alignment: .leading).padding(24).foregroundStyle(.white).background(Palette.ink, in: RoundedRectangle(cornerRadius: 24))
                        ForEach(Array(online.games.prefix(8).enumerated()), id: \.offset) { _, item in
                            VStack(alignment: .leading, spacing: 18) {
                                Text("FROM THE CLASS").font(.caption.bold()).tracking(2)
                                Image(systemName: "gamecontroller.fill").font(.system(size: 62)).foregroundStyle(.mint).frame(height: 84)
                                Text(item["name"] as? String ?? "Game").font(.title.bold()).lineLimit(2)
                                Text("Play with friends or computers.").font(.subheadline)
                                Button { play(item["id"] as? String ?? "") } label: { Label("Set up match", systemImage: "arrow.right").font(.headline).padding(12).background(.white.opacity(0.15), in: RoundedRectangle(cornerRadius: 12)) }
                            }.frame(width: 250, alignment: .leading).padding(24).foregroundStyle(.white).background(Palette.teal, in: RoundedRectangle(cornerRadius: 24))
                        }
                    }.scrollTargetLayout()
                }.scrollTargetBehavior(.viewAligned)
                Text("Class games").font(.title2.bold())
                Text("Start a shared match online. Open it once to save its game for offline play.").font(.subheadline).foregroundStyle(.secondary)
                if online.games.isEmpty {
                    Text("Connect to load the class catalog. Local Tic-Tac-Toe is always available.").foregroundStyle(.secondary)
                    Button("Browse online games") { play("") }.buttonStyle(.bordered)
                }
                ForEach(Array(online.games.enumerated()), id: \.offset) { _, item in
                    Button { play(item["id"] as? String ?? "") } label: {
                        HStack(spacing: 16) {
                            Image(systemName: "gamecontroller").font(.title2).foregroundStyle(Palette.teal)
                            VStack(alignment: .leading, spacing: 5) {
                                Text(item["name"] as? String ?? "Game").font(.headline)
                                if let description = item["description"] as? String, !description.isEmpty { Text(description).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                            }
                            Spacer(); Image(systemName: "chevron.right")
                        }.padding(18).background(.white, in: RoundedRectangle(cornerRadius: 18))
                    }.buttonStyle(.plain)
                }
            }.padding(24)
        }.background(Palette.paper).navigationTitle("Discover").navigationBarTitleDisplayMode(.inline)
            .refreshable { await online.refresh() }
    }
}

struct SetupView: View {
    @EnvironmentObject var store: PortalStore
    @State private var first = ""
    @State private var second = "Player 2"
    @State private var created: UUID?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Make room\nfor two.").font(.system(size: 36, weight: .bold, design: .rounded))
                VStack(alignment: .leading, spacing: 12) {
                    Label("Pass-and-play", systemImage: "iphone.gen3.radiowaves.left.and.right").font(.title3.bold())
                    Text("Share this phone. After each move, pass it to the other player.").foregroundStyle(.secondary)
                    Label("Works offline", systemImage: "checkmark.circle.fill").foregroundStyle(Palette.teal).font(.subheadline)
                }.padding(20).background(.white, in: RoundedRectangle(cornerRadius: 20))
                VStack(alignment: .leading, spacing: 12) {
                    Text("PLAYERS").font(.caption.bold()).tracking(2)
                    TextField("Player 1 · X", text: $first).accessibilityIdentifier("firstPlayer")
                    Divider()
                    TextField("Player 2 · O", text: $second).accessibilityIdentifier("secondPlayer")
                }.padding(20).background(.white, in: RoundedRectangle(cornerRadius: 20))
                PrimaryButton(title: "Start match  →") {
                    created = store.create(first: clean(first, fallback: store.guest), second: clean(second, fallback: "Player 2"))
                }.disabled(created != nil)
                Text("This match stays on this phone. For friends on other devices or computer opponents, use Online Play.").font(.footnote).foregroundStyle(.secondary)
            }.padding(24)
        }.background(Palette.paper).navigationTitle("Pass and play").navigationBarTitleDisplayMode(.inline)
            .onAppear { if first.isEmpty { first = store.guest } }
            .navigationDestination(item: $created) { id in MatchView(id: id) }
    }
    private func clean(_ value: String, fallback: String) -> String {
        let result = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return result.isEmpty ? fallback : String(result.prefix(30))
    }
}

struct MatchesView: View {
    @EnvironmentObject var store: PortalStore
    @State private var filter = "Active"
    private var filtered: [Match] { store.matches.filter { filter == "Finished" ? $0.isOver : !$0.isOver }.sorted { $0.updated > $1.updated } }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text("Your games on this phone.").font(.system(size: 34, weight: .bold, design: .rounded))
                Text("Pass-and-play matches are saved here. Find matches with friends on other devices or computers in Online Play.").font(.subheadline).foregroundStyle(.secondary)
                Picker("Matches", selection: $filter) { Text("Active").tag("Active"); Text("Finished").tag("Finished") }.pickerStyle(.segmented)
                if filtered.isEmpty {
                    ContentUnavailableView(filter == "Active" ? "Your table is open" : "No finished matches", systemImage: "rectangle.stack", description: Text(filter == "Active" ? "Start a match from Discover. Your progress will be saved here." : "Complete a game to see its result here."))
                }
                ForEach(filtered) { match in
                    NavigationLink { MatchView(id: match.id) } label: {
                        VStack(alignment: .leading, spacing: 12) {
                            HStack { Text("Tic-Tac-Toe").font(.title3.bold()); Spacer(); Image(systemName: "chevron.right") }
                            Text(match.names.joined(separator: " vs. ")).foregroundStyle(.secondary)
                            HStack {
                                Text(match.status).font(.subheadline.bold()).foregroundStyle(Palette.teal)
                                Spacer()
                                Text("LOCAL").font(.caption2.bold()).tracking(1)
                            }
                        }.padding(20).background(.white, in: RoundedRectangle(cornerRadius: 20))
                    }.buttonStyle(.plain)
                }
                NavigationLink { SetupView() } label: { Label("New match", systemImage: "plus.circle.fill").font(.headline).frame(maxWidth: .infinity).padding() }
            }.padding(24)
        }.background(Palette.paper).navigationTitle("Local Matches").navigationBarTitleDisplayMode(.inline)
    }
}

struct MatchView: View {
    let id: UUID
    @EnvironmentObject var store: PortalStore
    @Environment(\.dismiss) private var dismiss
    @State private var confirmHide = false
    var body: some View {
        Group {
            if let match = store.matches.first(where: { $0.id == id }) {
                ScrollView {
                    VStack(spacing: 20) {
                        HStack {
                            Label("LOCAL MATCH", systemImage: "iphone").font(.caption.bold())
                            Spacer()
                            Label("On this device", systemImage: "internaldrive").font(.caption)
                        }.foregroundStyle(.secondary)
                        Text(match.status).font(.system(.title, design: .rounded).bold()).multilineTextAlignment(.center)
                        HStack {
                            player(match.names[0], mark: "X", active: !match.isOver && match.turn == 0)
                            Text("vs").font(.caption).foregroundStyle(.secondary)
                            player(match.names[1], mark: "O", active: !match.isOver && match.turn == 1)
                        }
                        if match.awaitingHandoff && !match.isOver {
                            VStack(spacing: 22) {
                                Image(systemName: "hand.raised.fill").font(.system(size: 48)).foregroundStyle(Palette.teal)
                                Text("Pass the phone to\n\(match.names[match.turn])").font(.title2.bold()).multilineTextAlignment(.center)
                                Text("The board is hidden until the next player is ready.").foregroundStyle(.secondary).multilineTextAlignment(.center)
                                PrimaryButton(title: "I’m \(match.names[match.turn]) · Continue") { store.reveal(id: id) }
                            }.padding(24).frame(minHeight: 340).background(.white, in: RoundedRectangle(cornerRadius: 24))
                        } else {
                            GameWebView(match: match) { cell, player in store.move(id: id, cell: cell, player: player) }
                                .frame(height: 350).clipShape(RoundedRectangle(cornerRadius: 24))
                        }
                        if match.isOver {
                            Label(match.winner == nil ? "A well-matched pair." : "Nicely played!", systemImage: "flag.checkered").font(.headline)
                            Button("Hide finished match", role: .destructive) { confirmHide = true }.padding()
                        } else {
                            Text("Three in a row wins. You can leave this screen and resume from Local Matches.").font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                        }
                    }.padding(24)
                }
            } else { ContentUnavailableView("Match unavailable", systemImage: "rectangle.stack") }
        }.background(Palette.paper).navigationTitle("Tic-Tac-Toe").navigationBarTitleDisplayMode(.inline)
            .confirmationDialog("Hide this finished match from this device?", isPresented: $confirmHide, titleVisibility: .visible) {
                Button("Hide match", role: .destructive) { store.hide(id: id); dismiss() }
            }
    }
    private func player(_ name: String, mark: String, active: Bool) -> some View {
        VStack(spacing: 6) {
            Text(mark).font(.title2.bold()).foregroundStyle(mark == "X" ? Palette.teal : Palette.orange)
            Text(name).font(.subheadline.bold()).lineLimit(2)
        }.frame(maxWidth: .infinity).padding(12).background(active ? Palette.teal.opacity(0.10) : .white, in: RoundedRectangle(cornerRadius: 16))
    }
}


import WebKit
import Security

private struct OnlineIdentity: Codable { let id: String; let password: String; let display_name: String }
private struct OnlineMatch: Identifiable {
    let json: [String: Any]
    var id: String { json["id"] as? String ?? "" }
    var status: String { json["status"] as? String ?? "" }
    var players: [[String: Any]] { json["players"] as? [[String: Any]] ?? [] }
    var count: Int { json["move_count"] as? Int ?? 0 }
}

@MainActor private final class OnlineStore: ObservableObject {
    @Published var identity: OnlineIdentity?
    @Published var games: [[String: Any]] = []
    @Published var matches: [OnlineMatch] = []
    @Published var open: [OnlineMatch] = []
    @Published var selected: OnlineMatch?
    @Published var code = ""
    @Published var error: String?
    @Published var busy = false
    @Published var cacheError: String?
    @Published var selectedIsFresh = false
    @Published var pendingCounts: [String: Int] = [:]
    @Published var conflicts: [String: String] = [:]
    private var syncing = false
    private var resolving = false
    private func reloadSavedMatches() {
        guard let cache else { return }
        matches = cache.matches.map { OnlineMatch(json: $0) }
        pendingCounts = cache.pending.mapValues { $0.count }
        conflicts = cache.conflicts
        if let id = selected?.id, let snapshot = matches.first(where: { $0.id == id }) { selected = snapshot }
    }
    private var cache: OnlineCache?
    private let cacheDirectory: URL
    private let session: URLSession
    private var refreshing = false
    private var gameKey = ""
    private let key: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "edu.columbia.gameportal.online", kSecAttrAccount as String: "buildplay.fun"]
    init(identity providedIdentity: OnlineIdentity? = nil,
         cacheDirectory: URL = URL.applicationSupportDirectory.appending(path: "OnlineSnapshots", directoryHint: .isDirectory),
         session: URLSession = .shared) {
        self.cacheDirectory = cacheDirectory
        self.session = session
        if let providedIdentity { identity = providedIdentity }
        else {
            var query = key
            query[kSecReturnData as String] = true
            var result: CFTypeRef?
            if SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data {
                identity = try? JSONDecoder().decode(OnlineIdentity.self, from: data)
            }
        }
        restoreCache()
    }
    private func restoreCache() {
        guard let identity else { return }
        do {
            cache = try OnlineCache(directory: cacheDirectory, accountID: identity.id)
            games = cache?.games ?? []
            reloadSavedMatches()
        } catch { cacheError = "Saved online data could not be loaded: \(error.localizedDescription)" }
    }
    private func persist(_ operation: (OnlineCache) throws -> Void) {
        guard let cache else { return }
        do { try operation(cache); cacheError = nil }
        catch { cacheError = "Could not save for offline use: \(error.localizedDescription)" }
    }
    private func restoreSelectedCode() {
        guard let match = selected, let id = match.json["game_id"] as? String,
              let version = match.json["game_version"] as? Int else { return }
        code = cache?.code(gameID: id, version: version) ?? ""
        gameKey = code.isEmpty ? "" : "\(id)/\(version)"
    }
    func request(_ path: String, _ method: String = "GET", _ body: [String: Any]? = nil) async throws -> Any {
        var r = URLRequest(url: URL(string: "https://buildplay.fun" + path)!)
        r.httpMethod = method; r.timeoutInterval = 15; r.cachePolicy = .reloadIgnoringLocalCacheData
        if let identity {
            r.setValue(identity.id, forHTTPHeaderField: "X-User-Id")
            r.setValue(identity.password, forHTTPHeaderField: "X-User-Password")
        }
        if let body { r.httpBody = try JSONSerialization.data(withJSONObject: body); r.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let data: Data
        let response: URLResponse
        do { (data, response) = try await session.data(for: r) }
        catch { selectedIsFresh = false; throw error }
        let json = (try? JSONSerialization.jsonObject(with: data)) ?? [:]
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw NSError(domain: "Portal", code: (response as? HTTPURLResponse)?.statusCode ?? 0, userInfo: [NSLocalizedDescriptionKey: (json as? [String: Any])?["detail"] as? String ?? "The server rejected this request. Please refresh and try again."])
        }
        return json
    }
    func account(_ name: String) async {
        await act {
            let json = try await self.request("/users", "POST", ["display_name": name])
            let data = try JSONSerialization.data(withJSONObject: json)
            let user = try JSONDecoder().decode(OnlineIdentity.self, from: data)
            self.identity = user
            self.restoreCache()
            var item = self.key
            item[kSecValueData as String] = try JSONEncoder().encode(user)
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            let status = SecItemAdd(item as CFDictionary, nil)
            guard status == errSecSuccess else { throw NSError(domain: "Keychain", code: Int(status), userInfo: [NSLocalizedDescriptionKey: "Account created, but secure storage failed. Keep the app open for this session."]) }
        }
    }
    func act(_ operation: () async throws -> Void) async {
        guard !busy else { return }; busy = true; error = nil
        do { try await operation() } catch { self.error = error.localizedDescription }
        busy = false
        await refresh()
    }
    func refresh() async {
        guard !refreshing && !busy else { return }; refreshing = true
        defer { refreshing = false }
        do {
            if selected != nil { try await refreshSelected(); return }
            await syncPending()
            guard let catalog = try await request("/games") as? [[String: Any]] else { throw CocoaError(.coderInvalidValue) }
            games = catalog
            persist { try $0.saveGames(catalog) }
            if identity != nil {
                guard let snapshots = try await request("/matches") as? [[String: Any]] else { throw CocoaError(.coderInvalidValue) }
                let preserved = cache?.matches.filter { pendingCounts[$0["id"] as? String ?? "", default: 0] > 0 } ?? []
                let merged = snapshots.filter { pendingCounts[$0["id"] as? String ?? "", default: 0] == 0 } + preserved
                persist { try $0.saveMatches(merged) }
                reloadSavedMatches()
            }
            open = (try await request("/matches/open") as? [[String: Any]] ?? []).map { OnlineMatch(json: $0) }
            error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
    // Dedicated refresh for the open match. Errors propagate to the status card.
    func refreshSelected() async throws {
        guard let id = selected?.id else { return }
        do {
            await syncPending()
            guard let json = try await request("/matches/" + id) as? [String: Any], json["id"] as? String == id else {
                throw CocoaError(.coderInvalidValue)
            }
            guard selected?.id == id else { return }
            selectedIsFresh = pendingCounts[id, default: 0] == 0
            if selectedIsFresh {
                persist { try $0.saveMatch(json) }
                reloadSavedMatches()
                selected = OnlineMatch(json: json)
            }
            restoreSelectedCode()
            try await loadGame()
            error = nil
        } catch {
            if selected?.id == id { selectedIsFresh = false; self.error = error.localizedDescription }
            throw error
        }
    }
    func loadGame() async throws {
        guard let match = selected, match.status != "waiting_for_players", let id = match.json["game_id"] as? String, let version = match.json["game_version"] as? Int else { return }
        let key = "\(id)/\(version)"
        guard key != gameKey else { return }
        let game = try await request("/games/\(id)/versions/\(version)") as? [String: Any]
        guard let downloaded = game?["code"] as? String, !downloaded.isEmpty else { throw CocoaError(.coderInvalidValue) }
        persist { try $0.saveCode(downloaded, gameID: id, version: version) }
        guard selected?.id == match.id else { return }
        code = downloaded; gameKey = key
    }
    func choose(_ match: OnlineMatch) async {
        selected = match; selectedIsFresh = false; code = ""; gameKey = ""
        restoreSelectedCode()
        await refresh()
    }
    func create(_ game: String, computers: Int) async {
        await act {
            let json = try await self.request("/matches", "POST", ["game_id": game, "num_computer_opponents": computers]) as? [String: Any] ?? [:]
            self.selected = OnlineMatch(json: json); self.selectedIsFresh = false; self.code = ""; self.gameKey = ""
            self.persist { try $0.saveMatch(json) }
        }
    }
    private func recordResponse(_ response: Any, matchID: String) throws {
        guard let json = response as? [String: Any], json["id"] as? String == matchID else { throw CocoaError(.coderInvalidValue) }
        let snapshot = OnlineMatch(json: json)
        if selected?.id == matchID { selected = snapshot }
        if let index = matches.firstIndex(where: { $0.id == matchID }) { matches[index] = snapshot }
        else { matches.insert(snapshot, at: 0) }
        persist { try $0.saveMatch(json) }
    }
    func action(_ name: String) async {
        guard selectedIsFresh, let id = selected?.id else { return }
        await act {
            let response = try await self.request("/matches/\(id)/\(name)", "POST", [:])
            try self.recordResponse(response, matchID: id)
        }
    }
    var mySeat: Int? { selected?.players.first { $0["user_id"] as? String == identity?.id }?["player_index"] as? Int }
    var acting: Int? {
        guard !resolving, cache != nil, cacheError == nil, let match = selected, conflicts[match.id] == nil,
              match.status == "ongoing", let seat = mySeat else { return nil }
        let turns = match.json["turn_of_player_indices"] as? [Int] ?? []
        if turns.contains(seat) { return seat }
        return turns.first { i in match.players.contains { ($0["player_index"] as? Int) == i && ($0["kind"] as? String) == "computer" } }
    }
    var payload: [String: Any] {
        guard let m = selected else { return [:] }
        return ["type": "state_changed", "state": m.json["state"] ?? NSNull(), "players": m.players.map { p -> [String: Any] in
            var p = p; p["name"] = (p["kind"] as? String == "computer") ? "Computer" : ((p["user_id"] as? String == identity?.id) ? identity?.display_name ?? "You" : "Player \((p["player_index"] as? Int ?? 0) + 1)"); return p
        }, "turn_of_player_indices": m.json["turn_of_player_indices"] ?? NSNull(), "turn_of_player_index": (m.json["turn_of_player_indices"] as? [Int])?.first as Any? ?? NSNull(), "status": m.status, "end_reason": m.json["end_reason"] ?? NSNull(), "move_count": m.count, "my_player_index": mySeat as Any? ?? NSNull(), "acting_for_player_index": acting as Any? ?? NSNull()]
    }
    func move(_ message: [String: Any], matchID: String, count: Int) async {
        guard let cache, let m = selected, m.id == matchID, m.count == count, acting != nil,
              message["type"] as? String == "make_move", let state = message["new_state"] else { return }
        let turns: Any = message["next_turn_player_indices"] ?? (message["next_turn_player_index"] as? Int).map { [$0] } as Any? ?? NSNull()
        if !(turns is NSNull) {
            guard let indices = turns as? [Int], !indices.isEmpty, Set(indices).count == indices.count,
                  indices.allSatisfy({ index in m.players.contains { $0["player_index"] as? Int == index } }) else {
                error = "The game proposed an invalid next turn."; return
            }
        }
        let body: [String: Any] = ["new_state": state, "next_turn_player_indices": turns, "expected_move_count": count]
        var result = m.json
        result["state"] = state; result["turn_of_player_indices"] = turns
        result["move_count"] = count + 1
        result["status"] = turns is NSNull ? "over" : "ongoing"
        result["end_reason"] = turns is NSNull ? "finished" : NSNull()
        do {
            // Save intent and provisional state in one atomic write before displaying the move.
            try cache.enqueue(match: m.json, result: result, body: body)
            selectedIsFresh = false
            reloadSavedMatches()
            cacheError = nil
        } catch { cacheError = "Move was not saved: \(error.localizedDescription)"; return }
        await syncPending()
    }
    private func sameBase(_ server: [String: Any], _ base: [String: Any]) -> Bool {
        let fields = ["id", "game_id", "game_version", "players", "state", "move_count", "status", "turn_of_player_indices"]
        func encoded(_ value: [String: Any]) -> Data? {
            let reduced = Dictionary(uniqueKeysWithValues: fields.map { ($0, value[$0] ?? NSNull()) })
            return try? JSONSerialization.data(withJSONObject: reduced, options: [.sortedKeys])
        }
        guard let left = encoded(server), let right = encoded(base) else { return false }
        return left == right
    }
    func syncPending() async {
        guard !syncing, !resolving, let cache else { return }
        syncing = true
        defer { syncing = false }
        for id in cache.pending.keys.sorted() {
            guard cache.conflicts[id] == nil else { continue }
            while let entry = cache.pending[id]?.first {
                do {
                    guard let base = entry["base"] as? [String: Any], let body = entry["body"] as? [String: Any],
                          let entryID = entry["id"] as? String,
                          let server = try await request("/matches/" + id) as? [String: Any] else { throw CocoaError(.coderInvalidValue) }
                    // The backend has no idempotency key. A changed count (including a lost
                    // success response) needs review; never blindly replay or overwrite it.
                    guard sameBase(server, base) else {
                        try cache.markConflict(id, message: "The server match changed. Your pending moves are preserved. Review before using the server version.")
                        reloadSavedMatches(); break
                    }
                    guard let accepted = try await request("/matches/\(id)/moves", "POST", body) as? [String: Any],
                          accepted["id"] as? String == id,
                          accepted["move_count"] as? Int == (base["move_count"] as? Int ?? -1) + 1 else { throw CocoaError(.coderInvalidValue) }
                    try cache.acknowledge(matchID: id, entryID: entryID, server: accepted)
                    reloadSavedMatches()
                    if selected?.id == id { selectedIsFresh = pendingCounts[id, default: 0] == 0 }
                    error = nil
                } catch {
                    let failure = error as NSError
                    if failure.domain == "Portal" && (400...499).contains(failure.code) {
                        do { try cache.markConflict(id, message: "The server rejected this queue: \(failure.localizedDescription). Pending moves are preserved.") }
                        catch { cacheError = "Could not save sync conflict: \(error.localizedDescription)" }
                        reloadSavedMatches()
                        break
                    }
                    self.error = "Moves saved on this device; waiting to sync. \(error.localizedDescription)"
                    return
                }
            }
        }
    }
    func useServerVersion() async {
        guard !syncing, !resolving, let cache, let id = selected?.id, conflicts[id] != nil else { return }
        resolving = true
        defer { resolving = false }
        do {
            guard let server = try await request("/matches/" + id) as? [String: Any], server["id"] as? String == id else { throw CocoaError(.coderInvalidValue) }
            try cache.resolveConflict(id, server: server)
            reloadSavedMatches(); selectedIsFresh = true; error = nil
            restoreSelectedCode(); try await loadGame()
        } catch { self.error = error.localizedDescription }
    }
    func availableOffline(_ match: OnlineMatch) -> Bool {
        guard let id = match.json["game_id"] as? String, let version = match.json["game_version"] as? Int else { return false }
        return cache?.code(gameID: id, version: version) != nil
    }
    func title(_ m: OnlineMatch) -> String { games.first { $0["id"] as? String == m.json["game_id"] as? String }?["name"] as? String ?? "Game" }
}

struct OnlinePortalView: View {
    @EnvironmentObject private var store: OnlineStore
    @State private var name = ""
    @Binding var game: String
    @State private var computers = 1
    @State private var confirmServerVersion = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let error = store.error { Text(error).foregroundStyle(.red).font(.footnote); Button("Dismiss") { store.error = nil } }
                if let cacheError = store.cacheError { Text(cacheError).foregroundStyle(.red).font(.footnote) }
                if let user = store.identity {
                    Label("Playing as \(user.display_name)", systemImage: "person.crop.circle.badge.checkmark").font(.headline)
                    if let match = store.selected {
                        Button("← All online matches") { store.selected = nil; store.code = "" }
                        Text(store.title(match)).font(.largeTitle.bold())
                        Text(match.status.replacingOccurrences(of: "_", with: " ") + " · \(match.count) moves").foregroundStyle(.secondary)
                        if let conflict = store.conflicts[match.id] {
                            Text("Sync needs review").font(.headline).foregroundStyle(.orange)
                            Text(conflict).font(.subheadline)
                            Button("Use server version…") { confirmServerVersion = true }
                        } else if store.pendingCounts[match.id, default: 0] > 0 {
                            Label("\(store.pendingCounts[match.id, default: 0]) pending moves · saved on this device", systemImage: "clock.arrow.circlepath")
                                .foregroundStyle(Palette.teal)
                        } else if !store.selectedIsFresh {
                            Label("Saved match · moves will sync when connected", systemImage: "internaldrive")
                                .font(.subheadline).foregroundStyle(Palette.teal)
                        }
                        OnlineMatchControls(
                            matchURL: URL(string: "https://buildplay.fun/portal#match=\(match.id)")!,
                            refresh: { try await store.refreshSelected() },
                            phase: matchPhase(match),
                            moveCount: match.count,
                            pendingMoves: store.pendingCounts[match.id, default: 0]
                        )
                        if match.status == "waiting_for_players" {
                            Text("\(match.players.count) players seated")
                            if store.mySeat == nil && store.selectedIsFresh { Button("Join match") { Task { await store.action("join") } }.buttonStyle(.borderedProminent) }
                            if match.json["owner_user_id"] as? String == user.id && store.selectedIsFresh { Button("Start online match") { Task { await store.action("start") } }.buttonStyle(.borderedProminent) }
                        } else if !store.code.isEmpty {
                            Text(store.conflicts[match.id] != nil ? "Resolve the sync conflict to continue." : match.status == "over" ? "Match finished" : store.acting != nil ? "Your turn (or your computer’s turn)" : "Waiting for another player…").font(.subheadline)
                            SharedGameView(code: store.code, payload: store.payload) { message in
                                Task { await store.move(message, matchID: match.id, count: match.count) }
                            }.id("\(match.id)/\(match.json["game_version"] ?? 0)").frame(height: 520)
                        } else if store.error != nil {
                            Text("This game version is not saved on this device. Reconnect and open the match to download it.").foregroundStyle(.secondary)
                        } else { ProgressView("Loading game…") }
                    } else {
                        Text("Start a shared match.").font(.largeTitle.bold())
                        Picker("Game", selection: $game) {
                            Text("Choose a game").tag("")
                            ForEach(store.games.compactMap { $0["id"] as? String }, id: \.self) { id in Text(store.games.first { $0["id"] as? String == id }?["name"] as? String ?? id).tag(id) }
                        }
                        Stepper("Computer opponents: \(computers)", value: $computers, in: 0...9)
                        Text("Use 0 to invite another person. Choose a game’s supported player count before starting.").font(.caption).foregroundStyle(.secondary)
                        Button("Create online match") { Task { await store.create(game, computers: computers) } }.buttonStyle(.borderedProminent).disabled(game.isEmpty)
                        Text("Your shared matches").font(.title2.bold())
                        Text("Previously opened games stay available offline. Moves are saved on this phone and sync when you reconnect.").font(.subheadline).foregroundStyle(.secondary)
                        if store.matches.isEmpty { Text("No shared matches yet. Choose a game above to start one.") }
                        ForEach(store.matches) { match in matchButton(match) }
                        Text("Open matches").font(.title2.bold())
                        ForEach(store.open.filter { m in !m.players.contains { $0["user_id"] as? String == user.id } }) { match in matchButton(match) }
                    }
                } else {
                    Text("Join the class.").font(.largeTitle.bold())
                    Text("Create an online guest account on buildplay.fun. Your credentials are saved securely on this device.")
                    TextField("Online display name", text: $name).textFieldStyle(.roundedBorder).accessibilityIdentifier("onlineName")
                    Button("Create online account") { Task { await store.account(name.trimmingCharacters(in: .whitespacesAndNewlines)) } }.buttonStyle(.borderedProminent).disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                if store.busy { ProgressView() }
            }.frame(maxWidth: .infinity, alignment: .leading).padding(24).disabled(store.busy)
        }.background(Palette.paper).navigationTitle(store.selected == nil ? "Online Play" : "Match")
            .confirmationDialog("Use the server's match? Pending moves will be archived locally and removed from the sync queue.", isPresented: $confirmServerVersion, titleVisibility: .visible) {
                Button("Use server version") { Task { await store.useServerVersion() } }
            }
            .task(id: store.selected?.id) {
                if store.selected != nil { return } // Open matches use OnlineMatchControls polling.
                while !Task.isCancelled {
                    await store.refresh()
                    do { try await Task.sleep(nanoseconds: 2_000_000_000) } catch { break }
                }
            }
            .refreshable { await store.refresh() }
    }
    private func matchPhase(_ match: OnlineMatch) -> OnlineMatchPhase {
        if match.status == "waiting_for_players" { return .waitingForOpponent }
        if match.status == "over" || match.status == "finished" { return .finished }
        if store.mySeat == nil { return .spectator }
        if let seat = store.mySeat,
           (match.json["turn_of_player_indices"] as? [Int] ?? []).contains(seat) { return .yourTurn }
        return .opponentsTurn
    }
    private func matchButton(_ m: OnlineMatch) -> some View {
        Button { Task { await store.choose(m) } } label: {
            VStack(alignment: .leading) { Text(store.title(m)).bold(); Text(m.status.replacingOccurrences(of: "_", with: " ") + " · \(m.count) moves").font(.caption)
                if store.availableOffline(m) { Label("Available offline", systemImage: "internaldrive").font(.caption).foregroundStyle(.secondary) }
                if store.conflicts[m.id] != nil { Text("Sync needs review").foregroundStyle(.orange) }
                else if store.pendingCounts[m.id, default: 0] > 0 { Text("\(store.pendingCounts[m.id, default: 0]) pending moves").foregroundStyle(Palette.teal) } }.frame(maxWidth: .infinity, alignment: .leading).padding().background(.white, in: RoundedRectangle(cornerRadius: 12))
        }
    }
}

// Untrusted shared game HTML is isolated in an opaque-origin iframe with no network access.
private struct SharedGameView: UIViewRepresentable {
    let code: String
    let payload: [String: Any]
    let onMove: ([String: Any]) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.userContentController.add(context.coordinator, name: "sharedMove")
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        let encoded = Data(code.utf8).base64EncodedString()
        view.loadHTMLString("""
        <html><head><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-src about:"></head><body style="margin:0"><iframe id="game" sandbox="allow-scripts" style="border:0;width:100%;height:100vh"></iframe><script>
        const f=document.getElementById('game'); let latest=null,armed=false;
        window.receiveNative=(p)=>{latest=p;armed=true;f.contentWindow.postMessage(p,'*')};
        f.onload=()=>{if(latest)f.contentWindow.postMessage(latest,'*')};
        window.addEventListener('message',e=>{if(e.source===f.contentWindow&&armed&&e.data&&e.data.type==='make_move'){armed=false;window.webkit.messageHandlers.sharedMove.postMessage(e.data)}});
        f.srcdoc=new TextDecoder().decode(Uint8Array.from(atob('\(encoded)'),c=>c.charCodeAt(0)));
        </script></body></html>
        """, baseURL: nil)
        return view
    }
    func updateUIView(_ view: WKWebView, context: Context) { context.coordinator.parent = self; context.coordinator.send(view) }
    static func dismantleUIView(_ view: WKWebView, coordinator: Coordinator) { view.configuration.userContentController.removeScriptMessageHandler(forName: "sharedMove"); view.navigationDelegate = nil }
    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        var parent: SharedGameView
        var ready = false
        var sent: Data?
        init(_ parent: SharedGameView) { self.parent = parent }
        func webView(_ view: WKWebView, didFinish navigation: WKNavigation!) { ready = true; send(view) }
        func send(_ view: WKWebView) {
            guard ready, let data = try? JSONSerialization.data(withJSONObject: parent.payload, options: [.sortedKeys]), data != sent else { return }
            sent = data
            view.callAsyncJavaScript("window.receiveNative(payload)", arguments: ["payload": parent.payload], in: nil, in: .page) { _ in }
        }
        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.frameInfo.isMainFrame, let json = message.body as? [String: Any] else { return }
            sent = nil
            parent.onMove(json)
        }
        func webView(_ view: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            decisionHandler(action.request.url?.scheme == "about" ? .allow : .cancel)
        }
    }
}

// MARK: - Multiplayer invite and live-sync controls

/// State comes from the backend's current match response; the controls never
/// guess whose turn it is based on a timer or a local move counter.
enum OnlineMatchPhase: Equatable {
    case waitingForOpponent
    case yourTurn
    case opponentsTurn
    case finished
    case spectator

    var label: String {
        switch self {
        case .waitingForOpponent: return "Waiting for another player"
        case .yourTurn: return "Your turn"
        case .opponentsTurn: return "Opponent's turn"
        case .finished: return "Match finished"
        case .spectator: return "Watching match"
        }
    }

    var symbol: String {
        switch self {
        case .waitingForOpponent: return "person.crop.circle.badge.clock"
        case .yourTurn: return "hand.point.up.left.fill"
        case .opponentsTurn: return "hourglass"
        case .finished: return "flag.checkered"
        case .spectator: return "eye"
        }
    }
}

/// Usability controls for an existing server-backed match screen.
/// Requirements: iOS 16+, SwiftUI, an actual share URL, and a refresh closure.
/// IMPORTANT: Disable any other automatic polling for this match when using
/// autoRefreshEnabled=true. Do not include authentication tokens in share URLs.
struct OnlineMatchControls: View {
    let matchURL: URL
    let refresh: () async throws -> Void
    var phase: OnlineMatchPhase = .waitingForOpponent
    /// Pass the server's move count to indicate when a remote move appears.
    var moveCount: Int? = nil
    var pendingMoves = 0
    var autoRefreshEnabled: Bool = true
    var refreshIntervalSeconds: Double = 2

    @Environment(\.scenePhase) private var scenePhase
    @State private var isRefreshing = false
    @State private var lastError: String?
    @State private var lastUpdated: Date?
    @State private var didCopy = false
    @State private var showQRCode = false
    @State private var showNewMove = false
    @State private var previousMoveCount: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: phase.symbol)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(phase.label).font(.headline)
                    Text(statusDescription)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                if isRefreshing {
                    ProgressView().controlSize(.small)
                        .accessibilityLabel("Synchronizing match")
                } else {
                    Image(systemName: pendingMoves > 0 ? "clock.arrow.circlepath" : lastError == nil ? "checkmark.icloud" : "wifi.slash")
                        .foregroundStyle(lastError == nil && pendingMoves == 0 ? .green : .orange)
                        .accessibilityLabel(pendingMoves > 0 ? "Moves pending confirmation" : lastError == nil ? "Connection available" : "Connection error")
                }
            }

            if showNewMove {
                Label("Match updated", systemImage: "sparkles")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.blue)
                    .accessibilityAddTraits(.updatesFrequently)
            }

            if phase == .waitingForOpponent {
                Text("Invite a friend to join this match. They'll open the link in the class portal.")
                    .font(.subheadline)
            }

            LazyVGrid(columns: [GridItem(.adaptive(minimum: 125), spacing: 12)], spacing: 12) {
                ShareLink(item: matchURL) {
                    Label("Share", systemImage: "square.and.arrow.up").frame(maxWidth: .infinity).fixedSize(horizontal: false, vertical: true)
                }
                Button {
                    UIPasteboard.general.url = matchURL
                    didCopy = true
                } label: {
                    Label(didCopy ? "Copied" : "Copy", systemImage: "link").frame(maxWidth: .infinity).fixedSize(horizontal: false, vertical: true)
                }
                Button {
                    showQRCode = true
                } label: {
                    Label("QR Code", systemImage: "qrcode").frame(maxWidth: .infinity).fixedSize(horizontal: false, vertical: true)
                }
                Button {
                    Task { await refreshOnce() }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise").frame(maxWidth: .infinity).fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityLabel("Refresh match")
                .disabled(isRefreshing)
            }
            .buttonStyle(.bordered)

            if let lastError {
                VStack(alignment: .leading, spacing: 8) {
                    Label("Unable to sync", systemImage: "wifi.exclamationmark")
                        .font(.subheadline.weight(.semibold))
                    Text(lastError).font(.caption)
                    Button("Try Again") {
                        Task { await refreshOnce() }
                    }
                    .disabled(isRefreshing)
                }
                .foregroundStyle(.orange)
            } else if pendingMoves > 0 {
                Text("Moves saved locally; awaiting server confirmation.").font(.caption).foregroundStyle(.orange)
            } else if let lastUpdated {
                Text("Synced at \(lastUpdated.formatted(date: .omitted, time: .standard))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16))
        .sheet(isPresented: $showQRCode) {
            NavigationStack {
                VStack(spacing: 16) {
                    Text("Scan to join")
                        .font(.title2.bold())
                    if let image = qrCodeImage {
                        Image(uiImage: image)
                            .interpolation(.none)
                            .resizable()
                            .scaledToFit()
                            .frame(width: 250, height: 250)
                            .accessibilityLabel("QR code for match invitation")
                    } else {
                        Text("Couldn't generate QR code. Use Share or Copy instead.")
                    }
                    Text("The person scanning will open the match in the class web portal.")
                        .font(.subheadline)
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.secondary)
                }
                .padding()
                .navigationTitle("Invite player")
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") { showQRCode = false }
                    }
                }
            }
            .presentationDetents([.medium, .large])
        }
        .onAppear {
            previousMoveCount = moveCount
        }
        .onChange(of: moveCount) { newValue in
            defer { previousMoveCount = newValue }
            guard let newValue, let oldValue = previousMoveCount,
                  newValue > oldValue else { return }
            // A server-side counter advance is not necessarily an opponent move:
            // show this neutral indicator, including for computer turns.
            showNewMove = true
        }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await refreshOnce()
            guard autoRefreshEnabled else { return }
            while !Task.isCancelled {
                do {
                    try await Task.sleep(nanoseconds: UInt64(max(1, refreshIntervalSeconds) * 1_000_000_000))
                } catch { break }
                guard !Task.isCancelled else { break }
                await refreshOnce()
            }
        }
    }

    private var statusDescription: String {
        switch phase {
        case .waitingForOpponent: return "Invite someone to get started"
        case .yourTurn: return "Make your move in the game below"
        case .opponentsTurn: return "Updates automatically while you wait"
        case .finished: return "You can still share or review the result"
        case .spectator: return "Watching updates live"
        }
    }

    private var qrCodeImage: UIImage? {
        let context = CIContext()
        let generator = CIFilter.qrCodeGenerator()
        generator.message = Data(matchURL.absoluteString.utf8)
        generator.correctionLevel = "M"
        guard let output = generator.outputImage,
              let image = context.createCGImage(output, from: output.extent) else { return nil }
        return UIImage(cgImage: image)
    }

    @MainActor
    private func refreshOnce() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            try await refresh()
            guard !Task.isCancelled else { return }
            lastUpdated = Date()
            lastError = nil
        } catch is CancellationError {
            // Backgrounding or leaving the screen is not a network error.
        } catch {
            lastError = error.localizedDescription
        }
    }
}
