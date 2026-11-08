import ActivityKit
import Foundation

/// Starts, updates and ends Buddy's Live Activity - the part that lives in the Dynamic Island.
@MainActor
final class IslandController {
    static let shared = IslandController()

    private var activity: Activity<BuddyAttributes>?
    private var lastPush = Date.distantPast
    private var pendingState: BuddyAttributes.ContentState?
    private var flushTask: Task<Void, Never>?

    /// "Keep Buddy in the Dynamic Island": after an answer Buddy goes back to resting instead of leaving.
    var keepInIsland: Bool {
        get { UserDefaults.standard.object(forKey: "keepInIsland") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "keepInIsland") }
    }

    var enabled: Bool {
        ActivityAuthorizationInfo().areActivitiesEnabled
    }

    var isShowing: Bool {
        activity != nil || !Activity<BuddyAttributes>.activities.isEmpty
    }

    /// Show `state` in the island, starting the Live Activity if needed. Fast streams are throttled.
    func show(_ state: BuddyAttributes.ContentState, immediately: Bool = false) {
        adoptRunningActivity()
        guard enabled else { return }
        if activity == nil {
            start(state)
            return
        }
        pendingState = state
        let wait = 0.5 - Date().timeIntervalSince(lastPush)
        if immediately || wait <= 0 {
            flush()
        } else if flushTask == nil {
            flushTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
                self?.flush()
            }
        }
    }

    /// Put Buddy to rest in the island ("Tap to ask").
    func rest(message: String = "") {
        show(BuddyAttributes.ContentState(phase: .ready, answer: message), immediately: true)
    }

    /// Finished answer: stays readable, then either rests (keep in island) or leaves after a while.
    func finish(_ state: BuddyAttributes.ContentState) {
        show(state, immediately: true)
        guard !keepInIsland, let activity else { return }
        let content = ActivityContent(state: state, staleDate: nil)
        Task {
            await activity.end(content, dismissalPolicy: .after(Date().addingTimeInterval(10 * 60)))
        }
        self.activity = nil
    }

    func endAll() {
        flushTask?.cancel()
        flushTask = nil
        let running = Activity<BuddyAttributes>.activities
        activity = nil
        Task {
            for item in running {
                await item.end(nil, dismissalPolicy: .immediate)
            }
        }
    }

    // MARK: - internals

    private func adoptRunningActivity() {
        if activity == nil {
            activity = Activity<BuddyAttributes>.activities.first { $0.activityState == .active }
        }
        if let current = activity, current.activityState != .active {
            activity = nil
        }
    }

    private func start(_ state: BuddyAttributes.ContentState) {
        do {
            let content = ActivityContent(state: state, staleDate: nil, relevanceScore: 100)
            activity = try Activity.request(attributes: BuddyAttributes(), content: content, pushType: nil)
            lastPush = Date()
        } catch {
            NSLog("Buddy: could not start the Live Activity: \(error.localizedDescription)")
        }
    }

    private func flush() {
        flushTask?.cancel()
        flushTask = nil
        guard let activity, let state = pendingState else { return }
        pendingState = nil
        lastPush = Date()
        let content = ActivityContent(state: state, staleDate: nil, relevanceScore: state.phase.isBusy ? 100 : 50)
        Task {
            await activity.update(content)
        }
    }
}
