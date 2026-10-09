// AudioWorklet that forwards mono input frames to the main thread.
// Lives in public/ so it is a static, same-origin file (extension CSP forbids
// blob:/data: worklet modules).
class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor('recorder', RecorderProcessor);
