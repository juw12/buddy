import CoreGraphics
import Foundation

/// Pure helpers (no UI, no network) so they can be unit-tested: brain constants, the streaming
/// format, cleaning replies for the island, version comparison for update checks.
enum BuddyConfig {
    static let brainURL = URL(string: "https://buddy-brain.juwenmondial.workers.dev/v1/chat/completions")!
    static let healthURL = URL(string: "https://buddy-brain.juwenmondial.workers.dev/health")!
    static let model = "buddy-free"
    static let manifestURL = URL(string: "https://juw12.github.io/buddy/latest.json")!
    static let installPage = URL(string: "https://juw12.github.io/buddy/#iphone")!
    static let maxImageSide: CGFloat = 1280
    static let historyTurns = 4

    static let systemPrompt = """
    You are Buddy, a friendly expert helper that lives in the Dynamic Island at the top of the user's iPhone.
    Your reply is shown in the Dynamic Island and may be read aloud.

    Rules:
    - Reply in 1 to 4 short, natural sentences. No markdown, no lists, no emojis, no URLs.
    - If the user sent a screenshot or photo, be concrete and refer to what is actually visible in it,
      for example the exact button or menu name and where it is on the screen.
    - Do not add [POINT] tags or any other tags.
    """
}

enum StreamParser {
    /// One line of an OpenAI-style server-sent-event stream -> the new text in it (nil = nothing to add).
    static func piece(fromLine rawLine: String) -> String? {
        let line = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
        guard line.hasPrefix("data:") else { return nil }
        let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
        if payload.isEmpty || payload == "[DONE]" { return nil }
        guard let data = payload.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        if let choices = object["choices"] as? [[String: Any]], let first = choices.first {
            if let delta = first["delta"] as? [String: Any], let content = delta["content"] as? String {
                return content
            }
            if let message = first["message"] as? [String: Any], let content = message["content"] as? String {
                return content
            }
            return nil
        }
        return object["response"] as? String
    }

    /// A whole non-streamed JSON answer -> its text.
    static func fullReply(fromJSON data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = object["choices"] as? [[String: Any]],
              let message = choices.first?["message"] as? [String: Any] else { return nil }
        return message["content"] as? String
    }

    /// The brain's error body {"error": {"code": ..., "message": ...}} -> a friendly sentence.
    static func friendlyError(status: Int?, body: Data?) -> String {
        var code = ""
        if let body, let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
           let error = object["error"] as? [String: Any] {
            code = (error["code"] as? String) ?? ""
        }
        if code == "daily_cap" {
            return "My free brain is resting for today (daily limit reached). Try again tomorrow."
        }
        if code == "rate_limited" || status == 429 {
            return "Lots of questions at once! Give me a few seconds and ask again."
        }
        if let status, status > 0 {
            return "I can't reach my brain right now (error \(status)). Try again in a moment."
        }
        return "I can't reach my brain right now. Check your connection and try again."
    }
}

enum ReplyText {
    private static let pointTag = try! NSRegularExpression(pattern: "\\[\\s*POINT\\s*:[^\\]]*\\]", options: [.caseInsensitive])
    private static let markdown = try! NSRegularExpression(pattern: "[*_#`>]+")
    private static let spaces = try! NSRegularExpression(pattern: "\\s+")

    /// What the island shows: no [POINT] tags, no markdown symbols, single spaces, no dangling "[".
    static func clean(_ text: String) -> String {
        var result = replace(pointTag, in: text, with: "")
        if let bracket = result.firstIndex(of: "[") {
            result = String(result[..<bracket])
        }
        result = replace(markdown, in: result, with: "")
        result = replace(spaces, in: result, with: " ")
        return result.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Shortened for the island: whole words, at most `limit` characters, with an ellipsis.
    static func short(_ text: String, limit: Int) -> String {
        guard text.count > limit else { return text }
        let cut = text.prefix(limit)
        if let space = cut.lastIndex(of: " "), cut.distance(from: cut.startIndex, to: space) > limit / 2 {
            return String(cut[..<space]) + "\u{2026}"
        }
        return String(cut) + "\u{2026}"
    }

    private static func replace(_ regex: NSRegularExpression, in text: String, with template: String) -> String {
        let range = NSRange(text.startIndex..., in: text)
        return regex.stringByReplacingMatches(in: text, range: range, withTemplate: template)
    }
}

enum Versions {
    /// "v1.10.2" -> [1, 10, 2]
    static func parts(_ text: String) -> [Int] {
        var pieces = text.trimmingCharacters(in: .whitespaces)
            .trimmingCharacters(in: CharacterSet(charactersIn: "vV"))
            .split(separator: ".")
            .map { piece -> Int in Int(piece.filter(\.isNumber)) ?? 0 }
        while pieces.count < 3 { pieces.append(0) }
        return Array(pieces.prefix(3))
    }

    static func isNewer(_ remote: String, than local: String) -> Bool {
        let a = parts(remote)
        let b = parts(local)
        for index in 0..<3 where a[index] != b[index] {
            return a[index] > b[index]
        }
        return false
    }
}
