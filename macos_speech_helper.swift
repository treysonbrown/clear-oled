import AVFoundation
import Foundation
import Speech

final class SpeechStreamer {
  private let audioEngine = AVAudioEngine()
  private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
  private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
  private var recognitionTask: SFSpeechRecognitionTask?
  private var signalSources: [DispatchSourceSignal] = []
  private var isStopping = false
  private var finalTextSeen = ""

  func run() {
    do {
      try requestPermissions()
      try startAudioEngine()
      startRecognitionCycle()
      emit(["type": "ready"])
      installSignalHandlers()
      dispatchMain()
    } catch {
      emitError(code: "runtime_error", message: error.localizedDescription)
      exit(1)
    }
  }

  private func requestPermissions() throws {
    let micStatus = AVCaptureDevice.authorizationStatus(for: .audio)
    switch micStatus {
    case .authorized:
      break
    case .notDetermined:
      let semaphore = DispatchSemaphore(value: 0)
      var granted = false
      AVCaptureDevice.requestAccess(for: .audio) { value in
        granted = value
        semaphore.signal()
      }
      semaphore.wait()
      if !granted {
        throw SpeechHelperError.permissionDenied("Microphone access was denied.")
      }
    default:
      throw SpeechHelperError.permissionDenied("Microphone access is denied. Enable it for Terminal in System Settings > Privacy & Security > Microphone.")
    }

    let speechStatus = SFSpeechRecognizer.authorizationStatus()
    switch speechStatus {
    case .authorized:
      break
    case .notDetermined:
      let semaphore = DispatchSemaphore(value: 0)
      var granted = false
      SFSpeechRecognizer.requestAuthorization { status in
        granted = (status == .authorized)
        semaphore.signal()
      }
      semaphore.wait()
      if !granted {
        throw SpeechHelperError.permissionDenied("Speech recognition access was denied. Enable it in System Settings > Privacy & Security > Speech Recognition.")
      }
    default:
      throw SpeechHelperError.permissionDenied("Speech recognition is denied. Enable it in System Settings > Privacy & Security > Speech Recognition.")
    }
  }

  private func startAudioEngine() throws {
    let inputNode = audioEngine.inputNode
    let format = inputNode.outputFormat(forBus: 0)
    inputNode.removeTap(onBus: 0)
    inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
      self?.recognitionRequest?.append(buffer)
    }
    audioEngine.prepare()
    try audioEngine.start()
  }

  private func startRecognitionCycle() {
    recognitionTask?.cancel()
    recognitionTask = nil

    let request = SFSpeechAudioBufferRecognitionRequest()
    request.shouldReportPartialResults = true
    request.requiresOnDeviceRecognition = false
    recognitionRequest = request

    recognitionTask = recognizer?.recognitionTask(with: request) { [weak self] result, error in
      guard let self else { return }

      if let result {
        let text = result.bestTranscription.formattedString.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.isEmpty {
          if result.isFinal {
            if text != self.finalTextSeen {
              self.finalTextSeen = text
              self.emit(["type": "final", "text": text])
            }
            if !self.isStopping {
              DispatchQueue.main.async {
                self.startRecognitionCycle()
              }
            }
          } else {
            self.emit(["type": "partial", "text": text])
          }
        }
      }

      if let error, !self.isStopping {
        self.emitError(code: "runtime_error", message: error.localizedDescription)
        exit(1)
      }
    }
  }

  private func installSignalHandlers() {
    signal(SIGTERM, SIG_IGN)
    signal(SIGINT, SIG_IGN)

    let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
    term.setEventHandler { [weak self] in
      self?.stopAndExit()
    }
    term.resume()

    let interrupt = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
    interrupt.setEventHandler { [weak self] in
      self?.stopAndExit()
    }
    interrupt.resume()
    signalSources = [term, interrupt]
  }

  private func stopAndExit() {
    isStopping = true
    recognitionRequest?.endAudio()
    recognitionTask?.cancel()
    recognitionTask = nil
    recognitionRequest = nil
    audioEngine.stop()
    audioEngine.inputNode.removeTap(onBus: 0)
    exit(0)
  }

  private func emit(_ payload: [String: Any]) {
    guard JSONSerialization.isValidJSONObject(payload),
          let data = try? JSONSerialization.data(withJSONObject: payload, options: []),
          let text = String(data: data, encoding: .utf8) else {
      return
    }
    FileHandle.standardOutput.write(Data((text + "\n").utf8))
  }

  private func emitError(code: String, message: String) {
    emit(["type": "error", "code": code, "message": message])
  }
}

enum SpeechHelperError: LocalizedError {
  case permissionDenied(String)

  var errorDescription: String? {
    switch self {
    case let .permissionDenied(message):
      return message
    }
  }
}

let streamer = SpeechStreamer()
streamer.run()
