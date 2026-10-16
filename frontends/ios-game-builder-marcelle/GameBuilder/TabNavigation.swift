import SwiftUI

/// A tab's navigation. On iPhone (compact width) it's a stack: the list, then screens pushed
/// on top. On iPad (regular width) it's a split view: the list on the left and the chosen
/// game or match on the right, where it opens further screens (test play, a match).
/// `path[0]` is what the list picked; the rest is what was opened from it.
struct TabNavigation<ListContent: View>: View {
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Binding var path: [Route]
    let placeholder: String
    @ViewBuilder let list: () -> ListContent

    var body: some View {
        if sizeClass == .regular {
            NavigationSplitView {
                list()
                    .navigationSplitViewColumnWidth(min: 320, ideal: 380, max: 440)
                    .toolbar(.hidden, for: .navigationBar)
                    .environment(\.navigate) { path = [$0] }   // a new pick replaces the right side
                    .environment(\.selectedRoute, path.first?.key)
            } detail: {
                NavigationStack(path: rest) {
                    Group {
                        if let first = path.first { first.destination } else { Placeholder(text: placeholder) }
                    }
                    .environment(\.navigate) { path.append($0) }
                    // Screens pushed on the right get it directly: in a split view they don't
                    // inherit it from the stack.
                    .navigationDestination(for: Route.self) { route in
                        route.destination.environment(\.navigate) { path.append($0) }
                    }
                }
                .id(path.first?.key) // a new pick starts a fresh stack on the right
            }
            .navigationSplitViewStyle(.balanced)
            .environment(\.closeScreen) { if !path.isEmpty { path.removeLast() } }
        } else {
            NavigationStack(path: $path) {
                list()
                    .toolbar(.hidden, for: .navigationBar)
                    .navigationDestination(for: Route.self) { $0.destination }
            }
            .environment(\.navigate) { path.append($0) }
            .environment(\.closeScreen) { if !path.isEmpty { path.removeLast() } }
        }
    }

    /// The screens opened on the right side, on top of `path[0]`.
    private var rest: Binding<[Route]> {
        Binding(get: { Array(path.dropFirst()) },
                set: { path = path.prefix(1) + $0 })
    }
}

/// A list row that opens `route`; on iPad the open one is outlined in lime.
struct RouteButton<Label: View>: View {
    @Environment(\.navigate) private var navigate
    @Environment(\.selectedRoute) private var selected
    let route: Route
    @ViewBuilder let label: () -> Label

    var body: some View {
        Button { navigate(route) } label: { label() }
            .buttonStyle(.plain)
            .overlay {
                RoundedRectangle(cornerRadius: Theme.radius)
                    .strokeBorder(Theme.accent, lineWidth: 2)
                    .opacity(selected == route.key ? 1 : 0)
            }
    }
}

/// The right side on iPad before anything is picked.
private struct Placeholder: View {
    let text: String
    var body: some View {
        VStack(spacing: 12) {
            Wordmark(size: 28)
            Text(text).font(Theme.font(16)).foregroundStyle(Theme.muted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.background)
    }
}

extension Route {
    /// Identifies the screen (a game by id, so a new version is still the same screen).
    var key: String {
        switch self {
        case .game(let g): "game:\(g.id)"
        case .playSetup(let g): "setup:\(g.id)"
        case .play(let g, _): "play:\(g.id)"
        case .match(let id): "match:\(id)"
        }
    }
}

private struct SelectedRouteKey: EnvironmentKey { static let defaultValue: String? = nil }
private struct CloseScreenKey: EnvironmentKey { static let defaultValue: () -> Void = {} }

extension EnvironmentValues {
    /// The route open on the right side (iPad), to highlight its row.
    var selectedRoute: String? {
        get { self[SelectedRouteKey.self] }
        set { self[SelectedRouteKey.self] = newValue }
    }

    /// Closes the current screen: pops it on iPhone, clears the right side on iPad.
    var closeScreen: () -> Void {
        get { self[CloseScreenKey.self] }
        set { self[CloseScreenKey.self] = newValue }
    }
}

extension View {
    /// Keeps content at a comfortable reading width on big screens, centered.
    func readableWidth(_ width: CGFloat = 760) -> some View {
        frame(maxWidth: width).frame(maxWidth: .infinity)
    }
}
