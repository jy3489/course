import SwiftUI

/// Delete / hide / leave for one match, shared by the swipe action in the Matches list and
/// the "⋯" menu on the match screen. These need the network; offline they're disabled.
extension MatchesModel.Action {
    var title: String {
        switch self {
        case .delete: "delete"
        case .hide: "hide"
        case .leave: "leave"
        }
    }

    var systemImage: String {
        switch self {
        case .delete: "trash"
        case .hide: "eye.slash"
        case .leave: "rectangle.portrait.and.arrow.right"
        }
    }

    var tint: Color {
        switch self {
        case .delete: Theme.danger
        case .hide: Theme.surface2
        case .leave: Theme.warningBackground
        }
    }
}

/// Set `request` to a match to run its action: delete and leave ask first, hide doesn't.
/// `onDone` runs after the server confirmed (the match screen uses it to close itself).
struct MatchActionFlow: ViewModifier {
    @Environment(MatchesModel.self) private var model
    @Binding var request: Match?
    var onDone: () -> Void = {}
    @State private var confirming: Match?
    @State private var error: String?

    func body(content: Content) -> some View {
        content
            .onChange(of: request?.id) {
                guard let match = request else { return }
                request = nil
                if model.action(for: match) == .hide { run(match) } else { confirming = match }
            }
            .alert(confirming.map(title) ?? "", isPresented: isSet($confirming), presenting: confirming) { match in
                let action = model.action(for: match)
                Button(action == .delete ? "Delete" : "Leave", role: .destructive) { run(match) }
                Button("Cancel", role: .cancel) {}
            } message: { match in
                Text(message(match))
            }
            .alert("Couldn't do that", isPresented: isSet($error)) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(error ?? "")
            }
    }

    private func title(_ match: Match) -> String {
        model.action(for: match) == .delete ? "Delete this match for everyone?" : "Leave this match?"
    }

    private func message(_ match: Match) -> String {
        switch model.action(for: match) {
        case .delete:
            return "It disappears for all players, with its moves."
        case .leave where match.isWaiting:
            return "Your seat is freed for someone else."
        case .leave:
            let humans = match.players.filter { $0.kind == "human" }.count
            if humans <= 1 { return "You're the last person playing, so the match ends." }
            if model.game(for: match)?.allowsLeaveMidMatch == true {
                return "A computer takes over your seat and keeps playing."
            }
            return "This game doesn't allow leaving, so the match ends for everyone."
        default:
            return ""
        }
    }

    private func run(_ match: Match) {
        guard let action = model.action(for: match) else { return }
        Task {
            do {
                try await model.perform(action, on: match)
                onDone()
            } catch {
                self.error = error.localizedDescription
            }
        }
    }

    /// A Bool binding that's true while `value` is set; setting it to false clears it.
    private func isSet<T>(_ value: Binding<T?>) -> Binding<Bool> {
        Binding(get: { value.wrappedValue != nil }, set: { if !$0 { value.wrappedValue = nil } })
    }
}
