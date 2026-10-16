import GoogleSignIn
import SwiftUI

/// Sign in with Google: Google's sheet gives an ID token, buildplay.fun turns it into an
/// account (`POST /auth/google`), and the account goes into the Keychain.
struct LoginView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Wordmark(size: 34)
                    .padding(.top, 48)
                Text("Play the class games on buildplay.fun, online or offline.")
                    .font(Theme.font(17))
                    .foregroundStyle(Theme.muted)

                VStack(alignment: .leading, spacing: 12) {
                    SectionLabel(title: "log in")
                    Button(action: signInWithGoogle) {
                        if model.isLoading {
                            ProgressView().tint(Theme.accentText)
                        } else {
                            HStack(spacing: 10) {
                                // Google's official logo (Sign in with Google branding assets).
                                Image("GoogleLogo")
                                    .resizable()
                                    .frame(width: 22, height: 22)
                                    .clipShape(RoundedRectangle(cornerRadius: 3))
                                    .accessibilityHidden(true)
                                Text("sign in with google →")
                            }
                        }
                    }
                    .buttonStyle(.primary)
                    .disabled(model.isLoading)
                    .accessibilityIdentifier("google-sign-in")
                    Text("Same account as on the buildplay.fun website. It's saved in this phone's Keychain.")
                        .font(Theme.font(12))
                        .foregroundStyle(Theme.muted)
                }
                .themeCard(padding: 20)

                #if DEBUG
                if ProcessInfo.processInfo.arguments.contains("-guestLogin") { GuestLogin() }
                #endif

                if let error = model.errorMessage {
                    Text(error).font(Theme.font(14)).foregroundStyle(Theme.danger)
                }
            }
            .padding(.horizontal, 20)
            .readableWidth(520)
        }
        .background(Theme.background)
    }

    private func signInWithGoogle() {
        guard let presenter = Self.topViewController() else { return }
        Task {
            do {
                let result = try await GIDSignIn.sharedInstance.signIn(withPresenting: presenter)
                guard let idToken = result.user.idToken?.tokenString else {
                    model.errorMessage = "Google didn't return an ID token."
                    return
                }
                await model.logInWithGoogle(idToken: idToken)
            } catch let error as GIDSignInError where error.code == .canceled {
                // The user closed Google's sheet: nothing to show.
            } catch {
                model.errorMessage = error.localizedDescription
            }
        }
    }

    /// Google's sheet has to be presented from a UIKit view controller.
    private static func topViewController() -> UIViewController? {
        let scene = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
        var top = scene?.keyWindow?.rootViewController
        while let presented = top?.presentedViewController { top = presented }
        return top
    }
}

#if DEBUG
/// Hidden guest login for UI tests (launch argument `-guestLogin`), so tests never need a
/// Google account.
private struct GuestLogin: View {
    @Environment(AppModel.self) private var model
    @State private var name = ""

    private var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel(title: "debug: guest")
            TextField("", text: $name, prompt: Text("guest name").foregroundStyle(Theme.muted))
                .themeInput()
                .submitLabel(.go)
                .onSubmit(logIn)
                .accessibilityIdentifier("guest-name")
            Button("play as guest →", action: logIn)
                .buttonStyle(.outline)
                .disabled(trimmedName.isEmpty || model.isLoading)
                .accessibilityIdentifier("play-as-guest")
        }
        .themeCard(padding: 20)
    }

    private func logIn() {
        guard !trimmedName.isEmpty else { return }
        Task { await model.logInAsGuest(displayName: String(trimmedName.prefix(200))) }
    }
}
#endif
