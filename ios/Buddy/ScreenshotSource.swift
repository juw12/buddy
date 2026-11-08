import Photos
import UIKit

/// "Ask about my last screenshot": take a screenshot anywhere (side + volume up), open Buddy, tap once.
enum ScreenshotSource {
    enum Failure: LocalizedError {
        case notAllowed
        case none

        var errorDescription: String? {
            switch self {
            case .notAllowed:
                return "Let Buddy see your photos (Settings > Buddy > Photos) to ask about screenshots."
            case .none:
                return "No screenshot yet. Press the side button and volume up, then ask again."
            }
        }
    }

    static func latest() async throws -> UIImage {
        let status = await PHPhotoLibrary.requestAuthorization(for: .readWrite)
        guard status == .authorized || status == .limited else { throw Failure.notAllowed }

        let options = PHFetchOptions()
        options.predicate = NSPredicate(format: "(mediaSubtypes & %d) != 0", PHAssetMediaSubtype.photoScreenshot.rawValue)
        options.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]
        options.fetchLimit = 1
        guard let asset = PHAsset.fetchAssets(with: .image, options: options).firstObject else { throw Failure.none }

        let request = PHImageRequestOptions()
        request.deliveryMode = .highQualityFormat
        request.isNetworkAccessAllowed = true
        request.isSynchronous = false
        let target = CGSize(width: BuddyConfig.maxImageSide, height: BuddyConfig.maxImageSide)
        return try await withCheckedThrowingContinuation { continuation in
            var finished = false
            PHImageManager.default().requestImage(for: asset, targetSize: target, contentMode: .aspectFit,
                                                  options: request) { image, info in
                let degraded = (info?[PHImageResultIsDegradedKey] as? Bool) ?? false
                guard !finished, !degraded else { return }
                finished = true
                if let image {
                    continuation.resume(returning: image)
                } else {
                    continuation.resume(throwing: Failure.none)
                }
            }
        }
    }
}
