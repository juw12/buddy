import ActivityKit
import Foundation

/// What Buddy shows in the Dynamic Island and on the Lock Screen.
/// Shared by the app (which starts and updates the Live Activity) and the widget extension (which draws it).
struct BuddyAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        var phase: BuddyPhase
        var question: String
        var answer: String
        var updatedAt: Date

        init(phase: BuddyPhase, question: String = "", answer: String = "", updatedAt: Date = Date()) {
            self.phase = phase
            self.question = question
            self.answer = answer
            self.updatedAt = updatedAt
        }
    }

    /// Fixed for the life of one Live Activity.
    var name: String = "Buddy"
}

enum BuddyPhase: String, Codable, Hashable, CaseIterable {
    case ready        // resting in the island: tap to ask
    case looking      // reading a screenshot or photo
    case thinking     // waiting for the first words
    case answering    // words are streaming in
    case answer       // finished answer
    case trouble      // the brain could not be reached

    /// Short status for the compact island (right of the camera) and the expanded header.
    var status: String {
        switch self {
        case .ready: return "Tap to ask"
        case .looking: return "Looking"
        case .thinking: return "Thinking"
        case .answering: return "Answering"
        case .answer: return "Answer"
        case .trouble: return "Offline"
        }
    }

    /// SF Symbol for the compact trailing slot.
    var symbol: String {
        switch self {
        case .ready: return "sparkle"
        case .looking: return "eye"
        case .thinking: return "ellipsis"
        case .answering: return "text.bubble"
        case .answer: return "checkmark.circle.fill"
        case .trouble: return "wifi.exclamationmark"
        }
    }

    var isBusy: Bool {
        self == .looking || self == .thinking || self == .answering
    }
}

extension BuddyAttributes.ContentState {
    /// The text under the question in the expanded island / Lock Screen.
    var body: String {
        switch phase {
        case .ready:
            return answer.isEmpty ? "Tap to ask anything. Share a screenshot and I'll look at it." : answer
        case .looking:
            return "Looking at your screenshot\u{2026}"
        case .thinking:
            return "Thinking\u{2026}"
        case .answering, .answer, .trouble:
            return answer.isEmpty ? "\u{2026}" : answer
        }
    }

    /// The question line, or a friendly title when Buddy is idle.
    var title: String {
        question.isEmpty ? "Buddy" : "\u{201C}\(question)\u{201D}"
    }
}
