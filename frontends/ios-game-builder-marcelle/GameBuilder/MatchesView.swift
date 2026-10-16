import GoogleSignIn
import SwiftUI

/// The Matches tab: my matches (saved on the phone), open matches to join, join by link.
/// Swipe left on one of my matches to delete, hide or leave it.
struct MatchesView: View {
    @Environment(AppModel.self) private var app
    @Environment(MatchesModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var path: [Route] = []
    @State private var link = ""
    @State private var error: String?
    @State private var actionRequest: Match?

    var body: some View {
        TabNavigation(path: $path, placeholder: "Pick a match from the list.") {
            List {
                Group {
                    VStack(spacing: 0) {
                        AppHeader()
                        ThemeDivider()
                    }
                    .listRowInsets(EdgeInsets())
                    if app.isOffline {
                        Notice(text: "Offline: showing saved matches. Moves are sent later.", systemImage: "wifi.slash")
                            .padding(.top, 12)
                    }
                    if !model.pendingMoves.isEmpty {
                        let n = model.pendingMoves.count
                        Notice(text: "\(n) move\(n == 1 ? "" : "s") waiting to upload", systemImage: "arrow.up.circle")
                            .accessibilityIdentifier("moves-pending")
                    }
                    if let notice = model.notice {
                        Text(notice).font(Theme.font(14)).foregroundStyle(Theme.danger)
                    }
                    section("my matches", model.myMatches, swipe: true,
                            empty: "Create a match from a game's page (\"play online\"), or join one below.")
                    section("open matches", model.joinableMatches, swipe: false,
                            empty: app.isOffline ? "Not available offline." : "No open matches right now.")
                    joinByLink.padding(.top, 16)
                }
                .listRowInsets(EdgeInsets(top: 4, leading: 20, bottom: 4, trailing: 20))
                .listRowBackground(Theme.background)
                .listRowSeparator(.hidden)
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .refreshable { await model.refreshList() }
            .task { await model.refreshList() }
            .onChange(of: scenePhase) { if scenePhase == .active { Task { await model.refreshList() } } }
            .modifier(MatchActionFlow(request: $actionRequest))
            // A match deleted, hidden or left from the list disappears from the right side too.
            .onChange(of: model.myMatches.map(\.id)) {
                if case .match(let id) = path.first, model.match(id: id) == nil { path = [] }
            }
        }
    }

    @ViewBuilder
    private func section(_ title: String, _ matches: [Match], swipe: Bool, empty: String) -> some View {
        SectionLabel(title: title).padding(.top, 16)
        if matches.isEmpty { Text(empty).font(Theme.font(14)).foregroundStyle(Theme.muted) }
        ForEach(matches) { m in
            RouteButton(route: .match(m.id)) { MatchRow(match: m) }
                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                    if swipe { swipeButton(m) }
                }
        }
    }

    /// The match's one action, or a disabled "needs internet" while offline.
    @ViewBuilder
    private func swipeButton(_ match: Match) -> some View {
        if let action = model.action(for: match) {
            if app.isOffline {
                Button {} label: { Label("needs internet", systemImage: "wifi.slash") }
                    .tint(Theme.surface2)
                    .disabled(true)
            } else {
                Button { actionRequest = match } label: { Label(action.title, systemImage: action.systemImage) }
                    .tint(action.tint)
            }
        }
    }

    private var joinByLink: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(title: "join by link")
            HStack(spacing: 8) {
                TextField("", text: $link, prompt: Text("paste a match link").foregroundStyle(Theme.muted))
                    .themeInput()
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityIdentifier("match-link-field")
                Button("open") {
                    Task {
                        do { path = [.match(try await model.open(linkOrId: link).id)]; link = ""; error = nil }
                        catch { self.error = error.localizedDescription }
                    }
                }
                .buttonStyle(.outline)
                .frame(width: 90)
                .disabled(link.isEmpty || app.isOffline)
            }
            if let error { Text(error).font(Theme.font(13)).foregroundStyle(Theme.danger) }
        }
    }
}

private struct MatchRow: View {
    @Environment(MatchesModel.self) private var model
    let match: Match

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(model.game(for: match)?.name ?? match.gameId).font(Theme.font(17, .semibold)).foregroundStyle(Theme.text)
                Text(model.withWhom(match))
                    .font(Theme.font(13)).foregroundStyle(Theme.muted).lineLimit(1)
                HStack(spacing: 6) {
                    statusChip
                    Text("move \(match.moveCount)").font(Theme.mono(12)).foregroundStyle(Theme.muted)
                    if model.hasPendingMoves(match) {
                        Text("· not sent yet").font(Theme.mono(12)).foregroundStyle(Theme.warningText)
                    }
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right").font(.footnote).foregroundStyle(Theme.muted)
        }
        .themeCard(padding: 14)
    }

    /// Colored chips (dark text): your turn = lime, waiting = yellow, their turn and
    /// ongoing (a match I don't play in) = mint. "over" stays plain.
    private var statusChip: some View {
        let (text, color): (String, Color?) =
            match.isWaiting ? ("waiting for players", Theme.waitingChip)
            : match.isOver ? ("over", nil)
            : model.isMyTurn(match) ? ("your turn", Theme.accent)
            : model.mySeat(match) == nil ? ("ongoing", Theme.theirTurnChip)
            : ("their turn", Theme.theirTurnChip)
        return Text(text)
            .font(Theme.mono(12, bold: color != nil))
            .foregroundStyle(color == nil ? Theme.text : Theme.accentText)
            .padding(.horizontal, 10).padding(.vertical, 3)
            .background(color ?? Theme.surface2, in: Capsule())
            .overlay(Capsule().strokeBorder(color ?? Theme.line))
    }
}

/// The "game/builder" header with my avatar, shared by both tabs.
/// Tapping the avatar offers "log out".
struct AppHeader: View {
    @Environment(AppModel.self) private var app
    @Environment(MatchesModel.self) private var matches
    @State private var confirmLogOut = false

    var body: some View {
        HStack {
            Wordmark()
            Spacer()
            if let account = app.account {
                Menu {
                    Text(account.displayName)
                    Button("Log out", systemImage: "rectangle.portrait.and.arrow.right", role: .destructive) { confirmLogOut = true }
                } label: {
                    Avatar(name: account.displayName)
                }
                .accessibilityIdentifier("account-menu")
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .confirmationDialog("Log out?", isPresented: $confirmLogOut, titleVisibility: .visible) {
            Button("Log out", role: .destructive) {
                GIDSignIn.sharedInstance.signOut()
                matches.forgetAccount()
                app.logOut()
            }
        } message: {
            Text(unsentWarning)
        }
    }

    private var unsentWarning: String {
        let n = app.pendingEdits.count + matches.pendingMoves.count
        return n == 0 ? "You can log in again any time." : "\(n) change\(n == 1 ? "" : "s") not uploaded yet will be lost."
    }
}
