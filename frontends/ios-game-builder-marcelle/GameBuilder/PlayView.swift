import SwiftUI

/// Before a test match: pick the number of players and which seats are computers.
struct PlaySetupView: View {
    let game: Game
    @State private var count: Int
    @State private var kinds: [LocalMatch.Kind] = []

    init(game: Game) {
        self.game = game
        _count = State(initialValue: game.allowedPlayerCounts.first ?? 2)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                StepBar(active: .play)
                VStack(alignment: .leading, spacing: 6) {
                    Text("Test play").font(Theme.font(32, .extraBold, relativeTo: .largeTitle)).kerning(-0.9)
                    Text(game.name).font(Theme.font(17)).foregroundStyle(Theme.muted)
                }
                .foregroundStyle(Theme.text)

                VStack(alignment: .leading, spacing: 10) {
                    SectionLabel(title: "players")
                    countPicker
                }
                VStack(alignment: .leading, spacing: 10) {
                    SectionLabel(title: "seats")
                    ForEach(kinds.indices, id: \.self) { seat in seatRow(seat) }
                }
                .themeCard()

                Text("Moves stay on this phone. Nothing is sent to the server.")
                    .font(Theme.font(14)).foregroundStyle(Theme.muted)
                NavigationLink("start →", value: Route.play(game, kinds))
                    .buttonStyle(.primary)
                    .accessibilityIdentifier("start")
            }
            .padding(20)
        }
        .background(Theme.background)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .onAppear { if kinds.isEmpty { resize(to: count) } }
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

    private func seatRow(_ seat: Int) -> some View {
        HStack(spacing: 10) {
            Rectangle().fill(Theme.playerColor(seat)).frame(width: 10, height: 10)
            Text("Seat \(seat + 1)").font(Theme.font(16, .semibold)).foregroundStyle(Theme.text)
            Spacer()
            if kinds.count == 1 {
                // Solo: just me, no computer seat and no handing the phone over.
                Text("you (solo)").font(Theme.mono(13)).foregroundStyle(Theme.muted)
                    .accessibilityIdentifier("solo-seat")
            } else {
                seatKindPicker(seat)
            }
        }
    }

    private func seatKindPicker(_ seat: Int) -> some View {
        HStack(spacing: 0) {
            ForEach([LocalMatch.Kind.human, .computer], id: \.self) { kind in
                let selected = kinds[seat] == kind
                Button(kind.rawValue) { kinds[seat] = kind }
                    .font(Theme.mono(13, bold: selected))
                    .foregroundStyle(selected ? Theme.accentText : Theme.muted)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 34)
                    .background(selected ? Theme.accent : .clear)
                    .accessibilityIdentifier("seat\(seat)-\(kind.rawValue)")
            }
        }
        .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.line))
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
    }

    /// Keeps choices already made; new seats default to: seat 1 human, others computer.
    /// A single seat is always human.
    private func resize(to n: Int) {
        kinds = (0..<n).map { seat in seat < kinds.count ? kinds[seat] : (seat == 0 ? .human : .computer) }
        if n == 1 { kinds = [.human] }
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
