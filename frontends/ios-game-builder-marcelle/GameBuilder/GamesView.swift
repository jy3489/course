import SwiftUI

/// The screens that can be pushed on top of a tab's list.
enum Route: Hashable {
    case game(Game)
    case playSetup(Game)
    case play(Game, [LocalMatch.Kind])
    case match(String)

    @ViewBuilder var destination: some View {
        switch self {
        case .game(let game): GameDetailView(game: game)
        case .playSetup(let game): PlaySetupView(game: game)
        case .play(let game, let kinds): PlayView(game: game, kinds: kinds)
        case .match(let id): MatchView(matchId: id)
        }
    }
}

/// Lets a screen open another screen in its tab's navigation stack.
private struct NavigateKey: EnvironmentKey {
    static let defaultValue: (Route) -> Void = { _ in }
}

extension EnvironmentValues {
    var navigate: (Route) -> Void {
        get { self[NavigateKey.self] }
        set { self[NavigateKey.self] = newValue }
    }
}

struct GamesView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var path: [Route] = []
    @State private var creating = false

    private var myGames: [Game] { model.games.filter(model.isMine) }
    private var classGames: [Game] { model.games.filter { !model.isMine($0) } }

    var body: some View {
        TabNavigation(path: $path, placeholder: "Pick a game from the list.") {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    AppHeader()
                    ThemeDivider()
                    VStack(alignment: .leading, spacing: 20) {
                        if model.isOffline { Notice(text: offlineText, systemImage: "wifi.slash") }
                        if !model.pendingEdits.isEmpty {
                            let n = model.pendingEdits.count
                            Notice(text: "\(n) change\(n == 1 ? "" : "s") waiting to upload", systemImage: "arrow.up.circle")
                                .accessibilityIdentifier("pending-notice")
                        }
                        if let error = model.errorMessage, !model.isOffline {
                            Text(error).font(Theme.font(14)).foregroundStyle(Theme.danger)
                        }
                        Button("+ new game") { creating = true }
                            .buttonStyle(.primary)
                            .accessibilityIdentifier("new-game")
                        section("my games", games: myGames, empty: "Games you build appear here.")
                        section("class games", games: classGames, empty: emptyClassText)
                    }
                    .padding(20)
                }
            }
            .background(Theme.background)
            .refreshable { await model.refreshGames() }
            .onChange(of: scenePhase) { if scenePhase == .active { Task { await model.refreshGames() } } }
            .sheet(isPresented: $creating) { GameEditorView(game: nil) }
            .task {
                await model.refreshGames()
                #if DEBUG
                // For screenshots: `-openGame <id>` opens that game's page.
                let args = ProcessInfo.processInfo.arguments
                if path.isEmpty, let i = args.firstIndex(of: "-openGame"), i + 1 < args.count,
                   let game = model.games.first(where: { $0.id == args[i + 1] }) { path = [.game(game)] }
                #endif
            }
        }
    }

    private func section(_ title: String, games: [Game], empty: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(title: title)
            if games.isEmpty {
                Text(empty).font(Theme.font(14)).foregroundStyle(Theme.muted)
            }
            ForEach(games) { game in
                RouteButton(route: .game(game)) { GameRow(game: game, pending: model.isPending(game)) }
            }
        }
    }

    private var emptyClassText: String {
        if model.isLoading { return "Loading games…" }
        return "No games yet. Pull down to refresh."
    }

    private var offlineText: String {
        guard let date = model.gamesSavedAt else { return "Offline" }
        return "Offline: showing games saved \(date.formatted(.relative(presentation: .named)))"
    }
}

private struct GameRow: View {
    let game: Game
    let pending: Bool
    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(game.name).font(Theme.font(17, .semibold)).foregroundStyle(Theme.text)
                if !game.description.isEmpty {
                    Text(game.description).font(Theme.font(14)).foregroundStyle(Theme.muted).lineLimit(2)
                }
                HStack(spacing: 6) {
                    Text(playersText(game)).foregroundStyle(Theme.muted)
                    if pending { Text("· not uploaded yet").foregroundStyle(Theme.warningText) }
                    if !game.isPublished { UnpublishedTag() }
                }
                .font(Theme.mono(12, relativeTo: .caption))
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.footnote).foregroundStyle(Theme.muted)
        }
        .themeCard(padding: 14)
    }
}

func playersText(_ game: Game) -> String {
    let counts = game.allowedPlayerCounts
    if counts == [1] { return "1 player (solo)" }
    let range = counts.count > 2 && counts.last! - counts.first! == counts.count - 1
        ? "\(counts.first!)–\(counts.last!)" : counts.map(String.init).joined(separator: ", ")
    return "\(range) players"
}

/// Small pill for a game only its owner can see (unpublished from the web builder).
struct UnpublishedTag: View {
    var body: some View {
        Text("unpublished")
            .font(Theme.mono(11, bold: true))
            .foregroundStyle(Theme.warningText)
            .padding(.horizontal, 8).padding(.vertical, 2)
            .background(Theme.warningBackground, in: Capsule())
            .accessibilityIdentifier("unpublished-tag")
    }
}

/// A game's page: test play, details, code, and (for my games) edit.
struct GameDetailView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.closeScreen) private var closeScreen
    let initial: Game
    @State private var tab = Tab.details
    @State private var editing = false

    init(game: Game) { initial = game }

    /// The latest copy, so edits show up here right away.
    private var game: Game { model.current(initial) ?? initial }

    enum Tab: String, CaseIterable { case details, code }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                StepBar(active: .play)

                VStack(alignment: .leading, spacing: 6) {
                    Text(game.name).font(Theme.font(32, .extraBold, relativeTo: .largeTitle)).kerning(-0.9)
                        .foregroundStyle(Theme.text)
                    if !game.description.isEmpty {
                        Text(game.description).font(Theme.font(17)).foregroundStyle(Theme.muted)
                    }
                    HStack(spacing: 6) {
                        Text("\(playersText(game)) · \(model.isMine(game) ? "by you" : "class game")")
                            .font(Theme.mono(12, relativeTo: .caption)).foregroundStyle(Theme.muted)
                        if !game.isPublished { UnpublishedTag() }
                    }
                    .padding(.top, 4)
                }

                // One setup screen for every way to play: alone, pass-and-play, computers,
                // or friends online.
                NavigationLink("▶ play", value: Route.playSetup(game))
                    .buttonStyle(.primary)
                    .accessibilityIdentifier("test-play")
                if model.isMine(game) {
                    Button("✎ edit game") { editing = true }
                        .buttonStyle(.outline)
                        .accessibilityIdentifier("edit-game")
                }

                VStack(alignment: .leading, spacing: 14) {
                    tabs
                    switch tab {
                    case .details: details
                    case .code: code
                    }
                }
                .themeCard()

                changePanel
            }
            .padding(20)
            .readableWidth()
        }
        .background(Theme.background)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .sheet(isPresented: $editing) { GameEditorView(game: game, onDelete: { closeScreen() }) }
    }

    private var tabs: some View {
        HStack(spacing: 4) {
            ForEach(Tab.allCases, id: \.self) { t in
                Button(t.rawValue) { tab = t }
                    .font(Theme.mono(13, bold: tab == t))
                    .foregroundStyle(tab == t ? Theme.background : Theme.muted)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 34)
                    .background(tab == t ? Theme.text : .clear)
            }
        }
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 10) {
            detailRow("players", playersText(game))
            detailRow("version", game.version == 0 ? "not uploaded yet" : "\(game.version)")
            if model.isPending(game) { detailRow("changes", "waiting to upload") }
            detailRow("leave mid-match", game.allowsLeaveMidMatch ? "yes" : "no")
            detailRow("join mid-match", game.allowsJoinMidMatch ? "yes" : "no")
            detailRow("offline", "saved on this phone")
        }
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(Theme.mono(12)).foregroundStyle(Theme.muted)
            Spacer()
            Text(value).font(Theme.font(15)).foregroundStyle(Theme.text)
        }
    }

    private var code: some View {
        ScrollView([.horizontal, .vertical]) {
            Text(game.code)
                .font(Theme.mono(12))
                .foregroundStyle(Theme.text)
                .textSelection(.enabled)
                .padding(10)
        }
        .frame(height: 320)
        .background(Theme.codeBackground, in: RoundedRectangle(cornerRadius: Theme.radius))
    }

    private var changePanel: some View {
        LimeCard {
            Text("Change the game").font(Theme.font(24, .extraBold)).kerning(-0.5)
            Text("Coming later: describe a change and the AI updates the game.")
                .font(Theme.font(14)).foregroundStyle(Color(hex: 0x2C3519))
            SectionLabel(title: "versions", color: Theme.accentText, bold: true)
            Rectangle().fill(Theme.accentText).frame(height: 1.5)
            HStack(alignment: .firstTextBaseline) {
                Text(game.version == 0 ? "Not uploaded yet" : "Version \(game.version)").font(Theme.font(15, .semibold))
                Spacer()
                Text("current")
                    .font(Theme.mono(12, bold: true))
                    .foregroundStyle(Theme.accent)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Theme.accentText)
            }
        }
    }
}
