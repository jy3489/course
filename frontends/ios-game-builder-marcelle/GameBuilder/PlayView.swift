import SwiftUI

/// Before playing: how many players, and who sits in each seat. Seat 1 is always me; every
/// other seat is a person on this phone ("human", pass-and-play), a friend on their own
/// phone ("friend", invited with a link), or a computer. With no friends the match plays on
/// this phone; with friends it's an online match on the server.
struct PlaySetupView: View {
    @Environment(AppModel.self) private var app
    @Environment(MatchesModel.self) private var matches
    @Environment(\.navigate) private var navigate
    let game: Game
    @State private var count: Int
    /// Seats 2, 3, … (seat 1 is me).
    @State private var others: [SeatChoice] = []
    @State private var switchedToComputer = false
    @State private var busy = false
    @State private var error: String?

    enum SeatChoice: String, CaseIterable {
        case human, friend, computer
    }

    init(game: Game) {
        self.game = game
        _count = State(initialValue: game.allowedPlayerCounts.first ?? 2)
    }

    private var friends: Int { others.filter { $0 == .friend }.count }
    private var computers: Int { others.filter { $0 == .computer }.count }
    private var online: Bool { friends > 0 }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                StepBar(active: .play)
                VStack(alignment: .leading, spacing: 6) {
                    Text("Play").font(Theme.font(32, .extraBold, relativeTo: .largeTitle)).kerning(-0.9)
                    Text(game.name).font(Theme.font(17)).foregroundStyle(Theme.muted)
                }
                .foregroundStyle(Theme.text)

                VStack(alignment: .leading, spacing: 10) {
                    SectionLabel(title: "players")
                    countPicker
                }
                VStack(alignment: .leading, spacing: 12) {
                    SectionLabel(title: "seats")
                    youRow
                    ForEach(others.indices, id: \.self) { i in seatRow(i) }
                    if !others.isEmpty {
                        Text("human: takes turns on this phone · friend: plays on their own phone, invited by link · computer: the game plays it")
                            .font(Theme.font(12)).foregroundStyle(Theme.muted)
                    }
                    if online && others.count > 1 {
                        Text(switchedToComputer
                             ? "Friends play online on their own phones, so pass-and-play seats became computers."
                             : "With friends online, the other seats can be friends or computers (no pass-and-play).")
                            .font(Theme.font(12)).foregroundStyle(Theme.warningText)
                            .accessibilityIdentifier("friend-rule-note")
                    }
                }
                .themeCard()

                Text(online ? "Online: moves are shared with your friends." : "Moves stay on this phone. Nothing is sent to the server.")
                    .font(Theme.font(14)).foregroundStyle(Theme.muted)
                    .accessibilityIdentifier("play-mode-note")
                if online && app.isOffline {
                    Notice(text: "Inviting friends needs the internet.", systemImage: "wifi.slash")
                }
                if online && game.version == 0 {
                    Text("Upload the game first (it's waiting to upload).").font(Theme.font(13)).foregroundStyle(Theme.muted)
                }
                if let error { Text(error).font(Theme.font(14)).foregroundStyle(Theme.danger) }

                if online {
                    Button(action: invite) {
                        if busy { ProgressView().tint(Theme.accentText) } else { Text("invite friends →") }
                    }
                    .buttonStyle(.primary)
                    .disabled(busy || app.isOffline || game.version == 0)
                    .accessibilityIdentifier("invite-friends")
                } else {
                    NavigationLink("start →", value: Route.play(game, [.human] + others.map { $0 == .human ? .human : .computer }))
                        .buttonStyle(.primary)
                        .accessibilityIdentifier("start")
                }
            }
            .padding(20)
            .readableWidth()
        }
        .background(Theme.background)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .onAppear { if others.isEmpty { resize(to: count) } }
        .onChange(of: count) { resize(to: count) }
    }

    /// Like the web builder's player-count buttons: the chosen one is lime.
    private var countPicker: some View {
        HStack(spacing: 4) {
            ForEach(game.allowedPlayerCounts, id: \.self) { n in
                let selected = n == count
                Button("\(n)") { count = n }
                    .font(Theme.mono(15, bold: selected))
                    .foregroundStyle(selected ? Theme.accentText : Theme.text)
                    .frame(width: 40, height: 40)
                    .background(selected ? Theme.accent : Theme.background, in: RoundedRectangle(cornerRadius: Theme.radius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(selected ? Theme.accent : Theme.line))
                    .accessibilityLabel(n == 1 ? "1 player" : "\(n) players")
            }
        }
    }

    /// Seat 1: always me.
    private var youRow: some View {
        HStack(spacing: 10) {
            Rectangle().fill(Theme.playerColor(0)).frame(width: 10, height: 10)
            Text("Seat 1").font(Theme.font(16, .semibold)).foregroundStyle(Theme.text)
            Spacer()
            Text(others.isEmpty ? "you (solo)" : "you")
                .font(Theme.mono(13, bold: true)).foregroundStyle(Theme.accent)
                .accessibilityIdentifier("seat0-you")
        }
    }

    private func seatRow(_ i: Int) -> some View {
        let seat = i + 1
        return HStack(spacing: 10) {
            Rectangle().fill(Theme.playerColor(seat)).frame(width: 10, height: 10)
            Text("Seat \(seat + 1)").font(Theme.font(16, .semibold)).foregroundStyle(Theme.text)
                .lineLimit(1).fixedSize()
            Spacer(minLength: 4)
            HStack(spacing: 0) {
                ForEach(SeatChoice.allCases, id: \.self) { choice in
                    let selected = others[i] == choice
                    // With a friend online in another seat, nobody else can play on this phone.
                    let blocked = choice == .human && others.indices.contains { $0 != i && others[$0] == .friend }
                    Button(choice.rawValue) { choose(choice, for: i) }
                        .font(Theme.mono(13, bold: selected))
                        .foregroundStyle(selected ? Theme.accentText : Theme.muted)
                        .padding(.horizontal, 9)
                        .frame(minHeight: 36)
                        .background(selected ? Theme.accent : .clear)
                        .opacity(blocked ? 0.35 : 1)
                        .disabled(blocked)
                        .accessibilityIdentifier("seat\(seat)-\(choice.rawValue)")
                        .accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
            .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.line))
            .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
        }
    }

    /// Picking a friend makes it an online match: pass-and-play seats become computers.
    private func choose(_ choice: SeatChoice, for i: Int) {
        others[i] = choice
        if choice == .friend, others.contains(.human) {
            others = others.map { $0 == .human ? .computer : $0 }
            switchedToComputer = true
        }
        if !online { switchedToComputer = false }
        error = nil
    }

    /// Keeps choices already made; new seats start as computers.
    private func resize(to n: Int) {
        others = (1..<max(n, 1)).map { seat in seat - 1 < others.count ? others[seat - 1] : .computer }
    }

    /// Creates the online match and opens its waiting room.
    private func invite() {
        busy = true
        error = nil
        Task {
            do {
                let m = try await matches.create(game: game, friends: friends, computers: computers)
                navigate(.match(m.id))
            } catch {
                self.error = error.localizedDescription
            }
            busy = false
        }
    }
}

/// A test match on this phone: the game runs in a web view, this screen keeps the moves.
struct PlayView: View {
    let game: Game
    @State private var match: LocalMatch
    @State private var resendCount = 0
    @State private var error: String?

    init(game: Game, kinds: [LocalMatch.Kind]) {
        self.game = game
        _match = State(initialValue: LocalMatch(kinds: kinds))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            status
            GameWebView(code: game.code, stateJSON: match.stateChanged?.json(), resendCount: resendCount, onMessage: receive)
                .background(Theme.surface2)
                .overlay { if let seat = match.handoffTo { handoff(to: seat) } }
                .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
                .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.line))
            if let error {
                Text(error).font(Theme.mono(12)).foregroundStyle(Theme.danger)
            }
            HStack(spacing: 12) {
                Button("↶ undo") { error = nil; match.undo() }
                    .buttonStyle(.outline)
                    .disabled(match.moves.isEmpty)
                    .accessibilityIdentifier("undo")
                Button("↻ restart") { error = nil; match.restart(); resendCount += 1 }
                    .buttonStyle(.outline)
                    .accessibilityIdentifier("restart")
            }
        }
        .padding(16)
        .readableWidth(820) // on iPad landscape, don't spread the game over the whole width
        .background(Theme.background)
        .navigationTitle(game.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
    }

    private var status: some View {
        HStack(alignment: .firstTextBaseline) {
            TurnIndicator(text: statusText, color: match.actingSeat.map(Theme.playerColor) ?? Theme.accent)
            Spacer()
            Text("move \(match.moves.count)").font(Theme.mono(12)).foregroundStyle(Theme.muted)
        }
    }

    private var statusText: String {
        guard let seat = match.actingSeat else { return "Game over" }
        let player = match.players[seat]
        if player.kind == .computer { return "\(player.name) is thinking…" }
        return match.humanSeats.count > 1 ? "Your turn, \(player.name)" : "Your turn"
    }

    /// Covers the game until the next player has the phone, so they don't see the
    /// previous player's private view (and vice versa).
    private func handoff(to seat: Int) -> some View {
        let name = match.players[seat].name
        return VStack(spacing: 14) {
            Rectangle().fill(Theme.playerColor(seat)).frame(width: 14, height: 14)
            Text("Pass the phone to \(name)").font(Theme.font(24, .extraBold)).foregroundStyle(Theme.text)
                .multilineTextAlignment(.center)
            Text("No peeking!").font(Theme.font(15)).foregroundStyle(Theme.muted)
            Button("I'm \(name) →") { match.confirmHandoff() }
                .buttonStyle(.primary)
                .frame(maxWidth: 260)
                .accessibilityIdentifier("handoff-ready")
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.surface)
    }

    /// A message from the game. Only well-formed `make_move`s on an expected turn count.
    private func receive(_ json: String) {
        guard match.handoffTo == nil, !match.isOver else { return }
        guard let move = MakeMove(json: json) else {
            error = "Ignored a message that isn't a valid make_move: \(json.prefix(200))"
            return
        }
        if let problem = match.apply(newState: move.newState, nextTurn: move.nextTurn) {
            error = problem
            resendCount += 1 // send the unchanged state again so the game can retry
        } else {
            error = nil
        }
    }
}
