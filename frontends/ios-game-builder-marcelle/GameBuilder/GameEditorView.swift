import SwiftUI

/// Create or edit one of my games: name, description, player counts and code.
/// Saving works offline too: the change is shown at once and uploaded later.
struct GameEditorView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    /// nil = a new game.
    let game: Game?
    var onDelete: () -> Void = {}

    @State private var fields: GameFields
    @State private var confirmingDelete = false

    init(game: Game?, onDelete: @escaping () -> Void = {}) {
        self.game = game
        self.onDelete = onDelete
        _fields = State(initialValue: game?.fields
            ?? GameFields(name: "", description: "", allowedPlayerCounts: [2], code: GameTemplate.starter))
    }

    private var trimmedName: String { fields.name.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// The server refuses edits to an unpublished game: those go through its private draft
    /// in the web builder. Deleting still works.
    private var isUnpublished: Bool { game.map { !$0.isPublished } ?? false }

    private var problem: String? {
        if isUnpublished { return nil }
        if trimmedName.isEmpty { return "Give the game a name." }
        if trimmedName.count > 200 { return "The name can be at most 200 characters." }
        if fields.allowedPlayerCounts.isEmpty { return "Pick at least one player count." }
        if fields.code.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "The game needs code." }
        return nil
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if isUnpublished {
                        Notice(text: "This game is unpublished. Edit its private draft and publish it in the web builder; you can still delete it here.",
                               systemImage: "eye.slash")
                            .accessibilityIdentifier("unpublished-notice")
                    } else if model.isOffline {
                        Notice(text: "You're offline. Changes are saved on the phone and uploaded later.",
                               systemImage: "wifi.slash")
                    }
                    field("name") {
                        TextField("", text: $fields.name, prompt: Text("e.g. Connect Four").foregroundStyle(Theme.muted))
                            .themeInput()
                            .accessibilityIdentifier("name")
                    }
                    field("description") {
                        TextField("", text: $fields.description, prompt: Text("What is the game about?").foregroundStyle(Theme.muted), axis: .vertical)
                            .lineLimit(2...5)
                            .padding(.vertical, 10)
                            .themeInput()
                            .accessibilityIdentifier("description")
                    }
                    field("players") {
                        PlayerCountChips(selection: $fields.allowedPlayerCounts)
                        Text("How many players can play. Pick one or more.")
                            .font(Theme.font(12)).foregroundStyle(Theme.muted)
                    }
                    field("code") {
                        TextEditor(text: $fields.code)
                            .font(Theme.mono(12))
                            .foregroundStyle(Theme.text)
                            .scrollContentBackground(.hidden)
                            .autocorrectionDisabled()
                            .textInputAutocapitalization(.never)
                            .padding(8)
                            .frame(height: 320)
                            .background(Theme.codeBackground, in: RoundedRectangle(cornerRadius: Theme.radius))
                            .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.line))
                            .accessibilityIdentifier("code")
                        Text("One complete HTML document using state_changed / make_move (see the course README).")
                            .font(Theme.font(12)).foregroundStyle(Theme.muted)
                    }

                    if let problem {
                        Text(problem).font(Theme.font(14)).foregroundStyle(Theme.danger)
                    }
                    Button(game == nil ? "+ create game" : "save changes", action: save)
                        .buttonStyle(.primary)
                        .disabled(problem != nil || isUnpublished)
                        .accessibilityIdentifier("save")

                    if game != nil {
                        Button("delete game") { confirmingDelete = true }
                            .font(Theme.mono(14, bold: true))
                            .foregroundStyle(Theme.danger)
                            .frame(maxWidth: .infinity, minHeight: 44)
                            .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.danger.opacity(0.6)))
                            .accessibilityIdentifier("delete")
                    }
                }
                .padding(20)
            }
            .background(Theme.background)
            .navigationTitle(game == nil ? "New game" : "Edit game")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("cancel") { dismiss() }.font(Theme.mono(14))
                }
            }
            .confirmationDialog("Delete \(game?.name ?? "this game")?", isPresented: $confirmingDelete, titleVisibility: .visible) {
                Button("Delete game", role: .destructive, action: delete)
            } message: {
                Text("It disappears from the games list. Matches already played with it keep working.")
            }
        }
    }

    private func field(_ label: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(title: label)
            content()
        }
    }

    private func save() {
        guard problem == nil else { return }
        var f = fields
        f.name = trimmedName
        if let game { model.updateGame(game, f) } else { model.createGame(f) }
        dismiss()
    }

    private func delete() {
        guard let game else { return }
        model.deleteGame(game)
        dismiss()
        onDelete()
    }
}

/// Multi-select pill chips for 1...10 players (1 = a solo game); at least one stays selected.
struct PlayerCountChips: View {
    @Binding var selection: [Int]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 52), spacing: 8)], alignment: .leading, spacing: 8) {
            ForEach(1...10, id: \.self) { n in
                let on = selection.contains(n)
                Button { toggle(n) } label: {
                    Text("\(n)")
                        .font(Theme.mono(15, bold: on))
                        .foregroundStyle(on ? Theme.accentText : Theme.text)
                        .frame(maxWidth: .infinity, minHeight: 36)
                        .background(on ? Theme.accent : Theme.surface, in: Capsule())
                        .overlay(Capsule().strokeBorder(on ? Theme.accent : Theme.line))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(n == 1 ? "1 player" : "\(n) players")
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
    }

    private func toggle(_ n: Int) {
        if selection.contains(n) {
            guard selection.count > 1 else { return } // keep at least one
            selection.removeAll { $0 == n }
        } else {
            selection = (selection + [n]).sorted()
        }
    }
}
