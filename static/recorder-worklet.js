class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (ch) this.port.postMessage(ch.slice());
    return true;
  }
}
registerProcessor('recorder-processor', RecorderProcessor);
