import GoogleSignIn
import SwiftUI

/// The Matches tab: my matches (saved on the phone), open matches to join, join by link.
struct MatchesView: View {
    @Environment(AppModel.self) private var app
    @Environment(MatchesModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var path: [Route] = []
    @State private var link = ""
    @State private var error: String?

    var body: some View {
        NavigationStack(path: $path) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    AppHeader()
                    ThemeDivider()
                    VStack(alignment: .leading, spacing: 20) {
                        if app.isOffline { Notice(text: "Offline: showing saved matches. Moves are sent later.", systemImage: "wifi.slash") }
                        if !model.pendingMoves.isEmpty {
                            let n = model.pendingMoves.count
                            Notice(text: "\(n) move\(n == 1 ? "" : "s") waiting to upload", systemImage: "arrow.up.circle")
                                .accessibilityIdentifier("moves-pending")
                        }
                        if let notice = model.notice {
                            Text(notice).font(Theme.font(14)).foregroundStyle(Theme.danger)
                        }
                        section("my matches", model.myMatches, empty: "Create a match from a game's page (\"play online\"), or join one below.")
                        section("open matches", model.joinableMatches, empty: app.isOffline ? "Not available offline." : "No open matches right now.")
                        joinByLink
                    }
                    .padding(20)
                }
            }
            .background(Theme.background)
            .refreshable { await model.refreshList() }
            .task { await model.refreshList() }
            .onChange(of: scenePhase) { if scenePhase == .active { Task { await model.refreshList() } } }
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: Route.self) { $0.destination }
        }
        .environment(\.navigate) { path.append($0) }
    }

    private func section(_ title: String, _ matches: [Match], empty: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(title: title)
            if matches.isEmpty { Text(empty).font(Theme.font(14)).foregroundStyle(Theme.muted) }
            ForEach(matches) { m in
                NavigationLink(value: Route.match(m.id)) { MatchRow(match: m) }.buttonStyle(.plain)
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
                        do { path.append(.match(try await model.open(linkOrId: link).id)); link = ""; error = nil }
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
                Text(match.players.map { model.name(of: $0) }.joined(separator: " · "))
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

    private var statusChip: some View {
        let (text, lime): (String, Bool) =
            match.isWaiting ? ("waiting for players", false)
            : match.isOver ? ("over", false)
            : model.isMyTurn(match) ? ("your turn", true)
            : model.mySeat(match) == nil ? ("ongoing", false)
            : ("their turn", false)
        return Text(text)
            .font(Theme.mono(12, bold: lime))
            .foregroundStyle(lime ? Theme.accentText : Theme.text)
            .padding(.horizontal, 10).padding(.vertical, 3)
            .background(lime ? Theme.accent : Theme.surface2, in: Capsule())
            .overlay(Capsule().strokeBorder(lime ? Theme.accent : Theme.line))
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
