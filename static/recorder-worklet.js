class RecorderProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    this.port.postMessage(ch.slice());
    // calculate RMS for VAD
    let sum = 0;
    for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
    const rms = Math.sqrt(sum / ch.length);
    this.port.postMessage({ rms });
    return true;
  }
}
registerProcessor('recorder-processor', RecorderProcessor);
