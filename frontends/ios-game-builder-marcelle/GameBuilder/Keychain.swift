import Foundation
import Security

/// Saves the guest account (id + password) in the iOS Keychain, so it survives app restarts
/// and is never written to a plain file. The password can't be recovered from the server.
enum Keychain {
    /// One account per server ("buildplay.fun", or a debug server like "localhost-8000").
    private static var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: "edu.columbia.gamebuilder.marcelle",
         kSecAttrAccount as String: API.storageName]
    }

    static func load() -> Account? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(Account.self, from: data)
    }

    /// Forgets the account (debug `-resetLocalData`).
    static func delete() {
        SecItemDelete(query as CFDictionary)
    }

    static func save(_ account: Account) throws {
        SecItemDelete(query as CFDictionary)
        var item = query
        item[kSecValueData as String] = try JSONEncoder().encode(account)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(item as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw NSError(domain: "Keychain", code: Int(status), userInfo: [
                NSLocalizedDescriptionKey: "Could not save the account in the Keychain (status \(status)).",
            ])
        }
    }
}
