import GoogleSignIn
import SwiftUI

@main
struct GameBuilderApp: App {
    @State private var model: AppModel
    @State private var matches: MatchesModel

    init() {
        let model = AppModel()
        _model = State(initialValue: model)
        _matches = State(initialValue: MatchesModel(app: model))
    }

    var body: some Scene {
        WindowGroup {
            Group {
                if model.account == nil {
                    LoginView()
                } else {
                    TabView {
                        GamesView()
                            .tabItem { Label("Games", systemImage: "gamecontroller") }
                        MatchesView()
                            .tabItem { Label("Matches", systemImage: "person.2") }
                    }
                }
            }
            // Google's sign-in comes back to the app through its URL scheme (see Info.plist).
            .onOpenURL { GIDSignIn.sharedInstance.handle($0) }
            .environment(model)
            .environment(matches)
            .tint(Theme.accent)
            .preferredColorScheme(.dark) // always dark, like the web builder
        }
    }
}
