import SwiftUI
import UIKit

/// The "game night" look from my web GameBuilder: colors, fonts and the small reusable
/// pieces (buttons, cards, step bar...). Every screen takes its styling from here.
enum Theme {
    // MARK: Colors
    static let background = Color(hex: 0x10141F)
    static let surface = Color(hex: 0x171C2A)
    static let surface2 = Color(hex: 0x1C2233)
    static let text = Color(hex: 0xEEF0F5)
    static let muted = Color(hex: 0x9AA3B8)
    static let line = Color(hex: 0x2A3247)
    static let accent = Color(hex: 0xC6F432)       // lime
    static let accentText = Color(hex: 0x10141F)   // text on lime
    static let accentSoft = Color(hex: 0x232D1C)
    static let pink = Color(hex: 0xFF5FA2)
    static let board = Color(hex: 0x26304A)
    static let warningBackground = Color(hex: 0x3A2E10)
    static let warningText = Color(hex: 0xFFD98A)
    static let danger = Color(hex: 0xFF7A7A)
    static let codeBackground = Color(hex: 0x0C0F18)

    /// Seat colors, player 0 to 9.
    static let players: [Color] = [0xFF5FA2, 0xFFD84D, 0x5FD4FF, 0xC6F432, 0xB49BFF,
                                   0xFF9F4D, 0x3FE0C0, 0xFF6B6B, 0xA3ACC0, 0xD9A96C].map { Color(hex: $0) }
    static func playerColor(_ seat: Int) -> Color { players[seat % players.count] }
    /// Match status chips (the text on them is `accentText`).
    static let waitingChip = players[1]    // yellow: waiting for players
    static let theirTurnChip = players[6]  // mint: another player's turn, or ongoing

    // MARK: Shapes
    static let radius: CGFloat = 4

    // MARK: Fonts
    // Bundled in Fonts/ and listed under UIAppFonts in Info.plist.
    enum Weight: String { case regular = "Regular", medium = "Medium", semibold = "SemiBold", extraBold = "ExtraBold" }

    /// "Bricolage Grotesque": titles and body text.
    static func font(_ size: CGFloat, _ weight: Weight = .regular, relativeTo style: Font.TextStyle = .body) -> Font {
        custom("BricolageGrotesque-\(weight.rawValue)", size, style) {
            switch weight {
            case .regular: .system(size: size)
            case .medium: .system(size: size, weight: .medium)
            case .semibold: .system(size: size, weight: .semibold)
            case .extraBold: .system(size: size, weight: .heavy)
            }
        }
    }

    /// "Space Mono": buttons, labels and code.
    static func mono(_ size: CGFloat, bold: Bool = false, relativeTo style: Font.TextStyle = .body) -> Font {
        custom(bold ? "SpaceMono-Bold" : "SpaceMono-Regular", size, style) {
            .system(size: size, weight: bold ? .bold : .regular, design: .monospaced)
        }
    }

    /// Uses the bundled font, or the system font if it didn't load.
    private static func custom(_ name: String, _ size: CGFloat, _ style: Font.TextStyle, fallback: () -> Font) -> Font {
        UIFont(name: name, size: size) != nil ? .custom(name, size: size, relativeTo: style) : fallback()
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255, blue: Double(hex & 0xFF) / 255)
    }
}

// MARK: - Buttons

/// Solid lime button with dark Space Mono text, e.g. "+ new game".
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.mono(14, bold: true))
            .foregroundStyle(Theme.accentText)
            .frame(maxWidth: .infinity, minHeight: 44)
            .padding(.horizontal, 16)
            .background(Theme.accent, in: RoundedRectangle(cornerRadius: Theme.radius))
            .opacity(isEnabled ? (configuration.isPressed ? 0.85 : 1) : 0.5)
    }
}

/// Lime outline button for secondary actions.
struct OutlineButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.mono(14, bold: true))
            .foregroundStyle(Theme.accent)
            .frame(maxWidth: .infinity, minHeight: 44)
            .padding(.horizontal, 16)
            .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.accent, lineWidth: 1.5))
            .contentShape(Rectangle())
            .opacity(isEnabled ? (configuration.isPressed ? 0.7 : 1) : 0.5)
    }
}

extension ButtonStyle where Self == PrimaryButtonStyle { static var primary: PrimaryButtonStyle { .init() } }
extension ButtonStyle where Self == OutlineButtonStyle { static var outline: OutlineButtonStyle { .init() } }

// MARK: - Cards and inputs

extension View {
    /// Surface card: small corners and a 1pt border in the line color.
    func themeCard(padding: CGFloat = 16) -> some View {
        self.padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface, in: RoundedRectangle(cornerRadius: Theme.radius))
            .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.line, lineWidth: 1))
    }

    /// Text input look: dark background, 1pt border, small corners.
    func themeInput() -> some View {
        self.font(Theme.font(16))
            .foregroundStyle(Theme.text)
            .tint(Theme.accent)
            .padding(.horizontal, 14)
            .frame(minHeight: 44)
            .background(Theme.background, in: RoundedRectangle(cornerRadius: Theme.radius))
            .overlay(RoundedRectangle(cornerRadius: Theme.radius).strokeBorder(Theme.line, lineWidth: 1))
    }
}

/// The bright lime panel with dark text (used for "Change the game").
struct LimeCard<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 12) { content }
            .foregroundStyle(Theme.accentText)
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.accent, in: RoundedRectangle(cornerRadius: Theme.radius))
    }
}

// MARK: - Small pieces

/// "game/builder" with a lime slash.
struct Wordmark: View {
    var size: CGFloat = 22
    var body: some View {
        (Text("game") + Text("/").foregroundColor(Theme.accent) + Text("builder"))
            .font(Theme.font(size, .extraBold))
            .kerning(-0.02 * size)
            .foregroundStyle(Theme.text)
    }
}

/// Section label written like a code comment, e.g. "// my games".
struct SectionLabel: View {
    let title: String
    var color: Color = Theme.muted
    var bold = false
    var body: some View {
        Text("// \(title)").font(Theme.mono(12, bold: bold, relativeTo: .caption)).foregroundStyle(color)
    }
}

/// Round pink avatar with the user's initials.
struct Avatar: View {
    let name: String
    var size: CGFloat = 30
    private var initials: String {
        name.split(separator: " ").prefix(2).compactMap(\.first).map(String.init).joined().uppercased()
    }
    var body: some View {
        Text(initials.isEmpty ? "?" : initials)
            .font(Theme.font(size * 0.4, .extraBold))
            .foregroundStyle(Theme.accentText)
            .frame(width: size, height: size)
            .background(Theme.pink, in: Circle())
            .accessibilityLabel(name)
    }
}

/// Pill-shaped chip.
struct Chip: View {
    let text: String
    var body: some View {
        Text(text)
            .font(Theme.font(14))
            .foregroundStyle(Theme.text)
            .padding(.horizontal, 14)
            .padding(.vertical, 7)
            .background(Theme.surface, in: Capsule())
            .overlay(Capsule().strokeBorder(Theme.line, lineWidth: 1))
    }
}

/// "[1] describe · [2] play · [3] change"; the active step is a lime box.
struct StepBar: View {
    enum Step: Int, CaseIterable { case describe = 1, play, change }
    let active: Step
    var body: some View {
        HStack(spacing: 4) {
            ForEach(Step.allCases, id: \.self) { step in
                let isActive = step == active
                Text("[\(step.rawValue)] \(String(describing: step))")
                    .font(Theme.mono(13, bold: isActive))
                    .foregroundStyle(isActive ? Theme.accentText : Theme.muted)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(isActive ? Theme.accent : .clear)
                if step != .change { Spacer(minLength: 0) }
            }
        }
    }
}

/// Status line with a small colored square, e.g. "Your turn" with a pink square.
struct TurnIndicator: View {
    let text: String
    var color: Color = Theme.pink
    var body: some View {
        HStack(spacing: 10) {
            Rectangle().fill(color).frame(width: 10, height: 10)
            Text(text).font(Theme.font(19, .semibold)).foregroundStyle(Theme.text)
                .accessibilityIdentifier("turn-indicator")
        }
    }
}

/// Warning strip, e.g. the offline banner.
struct Notice: View {
    let text: String
    var systemImage: String?
    var body: some View {
        HStack(spacing: 8) {
            if let systemImage { Image(systemName: systemImage) }
            Text(text)
        }
        .font(Theme.font(14))
        .foregroundStyle(Theme.warningText)
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.warningBackground, in: RoundedRectangle(cornerRadius: Theme.radius))
    }
}

/// Thin divider in the line color.
struct ThemeDivider: View {
    var body: some View { Rectangle().fill(Theme.line).frame(height: 1) }
}
