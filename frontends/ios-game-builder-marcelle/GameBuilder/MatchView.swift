import CoreImage.CIFilterBuiltins
import SwiftUI

/// One online match. Before the start: seats, share link + QR, computers, start.
/// Then: the game, refreshed from the server every 2 seconds. Works offline with the saved copy.
struct MatchView: View {
    @Environment(AppModel.self) private var app
    @Environment(MatchesModel.self) private var model
    @Environment(\.closeScreen) private var closeScreen
    let matchId: String
    @State private var error: String?
    @State private var busy = false
    @State private var actionRequest: Match?
    @State private var copied = false

    var body: some View {
        Group {
            if let match = model.match(id: matchId) {
                if match.isWaiting { waitingRoom(match) } else { board(match) }
            } else {
                ProgressView().tint(Theme.accent).frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .background(Theme.background)
        .navigationTitle(model.match(id: matchId).flatMap { model.game(for: $0)?.name } ?? "Match")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { actionMenu } }
        .modifier(MatchActionFlow(request: $actionRequest, onDone: { closeScreen() }))
        .task {
            // Poll every 2 seconds while this screen is open (like the web portal).
            while !Task.isCancelled {
                await model.refresh(matchId: matchId)
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }

    /// "⋯": delete, hide or leave (whichever applies), disabled offline.
    @ViewBuilder private var actionMenu: some View {
        if let match = model.match(id: matchId), let action = model.action(for: match) {
            Menu {
                Section(app.isOffline ? "needs internet" : "") {
                    Button(action.title, systemImage: action.systemImage, role: action == .delete ? .destructive : nil) {
                        actionRequest = match
                    }
                    .disabled(app.isOffline)
                }
            } label: {
                Image(systemName: "ellipsis").foregroundStyle(Theme.text)
            }
            .accessibilityIdentifier("match-menu")
        }
    }

    // MARK: Playing

    private func board(_ match: Match) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                TurnIndicator(text: statusText(match), color: turnColor(match))
                Spacer()
                Text("move \(match.moveCount)").font(Theme.mono(12)).foregroundStyle(Theme.muted)
            }
            notices(match)
            if let game = model.game(for: match) {
                GameWebView(code: game.code, stateJSON: model.stateChanged(match).json()) { json in
                    guard let move = MakeMove(json: json) else { return }
                    model.makeMove(matchId: match.id, newState: move.newState, nextTurn: move.nextTurn)
                }
                .background(Theme.surface2)
                .clipShape(RoundedRectangle(cornerRadius: Theme.radius))
                .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.line))
            } else {
                Notice(text: "This game isn't saved on the phone yet. Connect to the internet once to download it.")
                Spacer()
            }
            if model.mySeat(match) == nil, !match.isOver {
                joinButton(match)
            }
        }
        .padding(16)
        .readableWidth(820)
    }

    private func statusText(_ match: Match) -> String {
        if match.isOver { return match.endReason == "player_left" ? "Over: a player left" : "Game over" }
        guard let mine = model.mySeat(match) else { return "Watching" }
        let turn = match.turnOfPlayerIndices ?? []
        if turn.contains(mine) { return "Your turn" }
        let names = turn.compactMap { i in match.players.indices.contains(i) ? model.name(of: match.players[i]) : nil }
        return names.isEmpty ? "Waiting" : "\(names.joined(separator: ", "))'s turn"
    }

    private func turnColor(_ match: Match) -> Color {
        guard let first = match.turnOfPlayerIndices?.first, !match.isOver else { return Theme.accent }
        return Theme.playerColor(first)
    }

    @ViewBuilder private func notices(_ match: Match) -> some View {
        if model.hasPendingMoves(match) {
            Notice(text: app.isOffline ? "Offline: your move is saved and will be sent when you're back online."
                                       : "Sending your move…", systemImage: "arrow.up.circle")
                .accessibilityIdentifier("move-pending")
        } else if app.isOffline {
            Notice(text: "Offline: showing the saved match.", systemImage: "wifi.slash")
        }
        if let notice = model.notice {
            HStack(alignment: .top) {
                Text(notice).font(Theme.font(14)).foregroundStyle(Theme.danger)
                    .accessibilityIdentifier("match-notice")
                Spacer()
                Button("ok") { model.notice = nil }.font(Theme.mono(13, bold: true)).foregroundStyle(Theme.accent)
            }
        }
        if let error { Text(error).font(Theme.font(14)).foregroundStyle(Theme.danger) }
    }

    // MARK: Waiting for players

    /// Before the start. A match I created from the play setup screen knows how many friend
    /// seats I planned, so it shows them one by one; any other match shows the general room.
    @ViewBuilder private func waitingRoom(_ match: Match) -> some View {
        if match.ownerUserId == app.account?.id, let planned = model.plannedFriendSeats(match) {
            friendsWaitingRoom(match, planned: planned)
        } else {
            openWaitingRoom(match)
        }
    }

    /// Seats in the server's real order: people in the order they joined (me first), then
    /// the friend seats still open, then the computers.
    private func friendsWaitingRoom(_ match: Match, planned: Int) -> some View {
        let game = model.game(for: match)
        let me = app.account?.id
        let people = match.players.filter { $0.kind == "human" }
        let friendsIn = people.filter { $0.userId != me }.count
        let open = max(0, planned - friendsIn)
        let computers = match.players.filter { $0.kind == "computer" }
        let counts = game?.allowedPlayerCounts ?? []
        let ready = open == 0 && counts.contains(match.players.count)
        let url = API.shareURL(matchId: match.id)
        return ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(open == 0 ? "Everyone's here" : "Waiting for friends")
                        .font(Theme.font(28, .extraBold)).kerning(-0.8).foregroundStyle(Theme.text)
                    Text("\(game?.name ?? "Game") · \(people.count + open + computers.count) seats")
                        .font(Theme.mono(12)).foregroundStyle(Theme.muted)
                }
                notices(match)

                VStack(alignment: .leading, spacing: 14) {
                    SectionLabel(title: "seats")
                    ForEach(Array(people.enumerated()), id: \.offset) { i, seat in
                        seatLine(i) {
                            Text(model.name(of: seat)).font(Theme.font(16, .semibold)).foregroundStyle(Theme.text)
                            Spacer()
                            if seat.userId == me {
                                Text("you").font(Theme.mono(12, bold: true)).foregroundStyle(Theme.accent)
                            } else {
                                Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.accent)
                                    .accessibilityLabel("joined")
                                    .accessibilityIdentifier("friend-joined")
                            }
                        }
                    }
                    ForEach(0..<open, id: \.self) { k in
                        waitingFriend(index: people.count + k, url: url)
                    }
                    ForEach(computers.indices, id: \.self) { j in
                        seatLine(people.count + open + j) {
                            // No number: a computer's seat number moves up as friends join.
                            Text("Computer").font(Theme.font(16, .semibold)).foregroundStyle(Theme.text)
                            Spacer()
                            Text("the game plays it").font(Theme.mono(12)).foregroundStyle(Theme.muted)
                        }
                    }
                    Text("The server seats people in the order they join, then the computers.")
                        .font(Theme.font(12)).foregroundStyle(Theme.muted)
                }
                .themeCard()

                VStack(alignment: .leading, spacing: 6) {
                    Text("Friends open the link in any browser or the iPhone app, then tap Join.")
                        .font(Theme.font(14)).foregroundStyle(Theme.muted)
                    Text(url.absoluteString)
                        .font(Theme.mono(11)).foregroundStyle(Theme.muted)
                        .textSelection(.enabled)
                        .accessibilityIdentifier("match-link")
                }

                Button("start match →") { run { try await model.start(matchId: match.id) } }
                    .buttonStyle(.primary)
                    .disabled(!ready || busy || app.isOffline)
                    .accessibilityIdentifier("start-match")
                if open > 0 {
                    Text("Starts when \(open == 1 ? "your friend joins" : "\(open) more friends join").")
                        .font(Theme.font(13)).foregroundStyle(Theme.muted)
                }
                Text("Online: moves are shared with your friends.")
                    .font(Theme.font(14)).foregroundStyle(Theme.muted)
            }
            .padding(20)
            .readableWidth()
        }
    }

    /// One seat line: the seat's color, then its content.
    private func seatLine<Content: View>(_ index: Int, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 10) {
            Rectangle().fill(Theme.playerColor(index)).frame(width: 10, height: 10)
            content()
        }
    }

    /// An open friend seat: the invitation (QR code, share sheet, copy).
    private func waitingFriend(index: Int, url: URL) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            seatLine(index) {
                Text("waiting for friend…").font(Theme.font(16, .semibold)).foregroundStyle(Theme.muted)
                    .accessibilityIdentifier("friend-waiting")
                Spacer()
            }
            HStack(alignment: .center, spacing: 12) {
                QRCode(text: url.absoluteString)
                    .frame(width: 84, height: 84)
                    .accessibilityLabel("QR code for the match link")
                VStack(spacing: 8) {
                    ShareLink(item: url) { Text("share link") }.buttonStyle(.outline)
                    Button(copied ? "copied ✓" : "copy") {
                        UIPasteboard.general.string = url.absoluteString
                        copied = true
                    }
                    .buttonStyle(.outline)
                }
            }
            .padding(.leading, 20)
        }
    }

    private func openWaitingRoom(_ match: Match) -> some View {
        let game = model.game(for: match)
        let isOwner = match.ownerUserId == app.account?.id
        let computers = match.players.filter { $0.kind == "computer" }.count
        let counts = game?.allowedPlayerCounts ?? []
        let canStart = counts.contains(match.players.count)
        return ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Waiting for players").font(Theme.font(28, .extraBold)).kerning(-0.8).foregroundStyle(Theme.text)
                    Text("\(game?.name ?? "Game") · \(match.players.count) seat\(match.players.count == 1 ? "" : "s") · plays \(counts.map(String.init).joined(separator: ", "))")
                        .font(Theme.mono(12)).foregroundStyle(Theme.muted)
                }
                notices(match)

                VStack(alignment: .leading, spacing: 10) {
                    SectionLabel(title: "seats")
                    ForEach(match.players, id: \.playerIndex) { seat in
                        HStack(spacing: 10) {
                            Rectangle().fill(Theme.playerColor(seat.playerIndex)).frame(width: 10, height: 10)
                            Text(model.name(of: seat)).font(Theme.font(16, .semibold)).foregroundStyle(Theme.text)
                            if seat.userId == app.account?.id { Text("you").font(Theme.mono(12)).foregroundStyle(Theme.muted) }
                            Spacer()
                            Text(seat.kind).font(Theme.mono(12)).foregroundStyle(Theme.muted)
                        }
                    }
                    if isOwner {
                        HStack {
                            Text("computers").font(Theme.mono(12)).foregroundStyle(Theme.muted)
                            Spacer()
                            Stepper("\(computers)", value: Binding(get: { computers }, set: { n in
                                run { try await model.setComputers(matchId: match.id, n) }
                            }), in: 0...max(0, (counts.max() ?? 2) - 1))
                            .font(Theme.mono(15, bold: true))
                            .fixedSize()
                            .disabled(busy || app.isOffline)
                            .accessibilityIdentifier("computers")
                        }
                    }
                }
                .themeCard()

                shareCard(match)

                if isOwner {
                    Button("start match →") { run { try await model.start(matchId: match.id) } }
                        .buttonStyle(.primary)
                        .disabled(!canStart || busy || app.isOffline)
                        .accessibilityIdentifier("start-match")
                    if !canStart {
                        Text("To start, the number of seats must be \(counts.map(String.init).joined(separator: " or ")). Add computers or wait for friends to join.")
                            .font(Theme.font(13)).foregroundStyle(Theme.muted)
                    }
                } else if model.mySeat(match) == nil {
                    joinButton(match)
                } else {
                    Text("Waiting for the owner to start the match.").font(Theme.font(15)).foregroundStyle(Theme.muted)
                }
            }
            .padding(20)
            .readableWidth()
        }
    }

    private func shareCard(_ match: Match) -> some View {
        let url = API.shareURL(matchId: match.id)
        return VStack(alignment: .leading, spacing: 12) {
            SectionLabel(title: "invite friends")
            HStack(alignment: .top, spacing: 14) {
                QRCode(text: url.absoluteString)
                    .frame(width: 120, height: 120)
                    .accessibilityLabel("QR code for the match link")
                VStack(alignment: .leading, spacing: 8) {
                    Text("Scan to open the match on buildplay.fun, or share the link.")
                        .font(Theme.font(13)).foregroundStyle(Theme.muted)
                    Text(url.absoluteString)
                        .font(Theme.mono(11)).foregroundStyle(Theme.text)
                        .textSelection(.enabled)
                        .accessibilityIdentifier("match-link")
                }
            }
            HStack(spacing: 10) {
                ShareLink(item: url) { Text("share link") }.buttonStyle(.outline)
                Button("copy") { UIPasteboard.general.string = url.absoluteString }.buttonStyle(.outline)
            }
        }
        .themeCard()
    }

    private func joinButton(_ match: Match) -> some View {
        Button("+ join this match") { run { _ = try await model.join(matchId: match.id) } }
            .buttonStyle(.primary)
            .disabled(busy || app.isOffline)
            .accessibilityIdentifier("join-match")
    }

    private func run(_ action: @escaping () async throws -> Void) {
        busy = true
        Task {
            do { try await action(); error = nil } catch { self.error = error.localizedDescription }
            busy = false
        }
    }
}

/// A QR code image for a text (Core Image, no packages).
struct QRCode: View {
    let text: String
    var body: some View {
        if let image = Self.image(text) {
            Image(uiImage: image).interpolation(.none).resizable().scaledToFit()
                .padding(6).background(.white, in: RoundedRectangle(cornerRadius: Theme.radius))
        }
    }

    private static func image(_ text: String) -> UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        guard let output = filter.outputImage,
              let cg = CIContext().createCGImage(output, from: output.extent) else { return nil }
        return UIImage(cgImage: cg)
    }
}
