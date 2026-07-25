export interface FftConfig {
  channel_index: number;
  sample_frequency_hz: number;
  base_frequency_hz: number;
  harmonic_count: number;
  window: "rectangular" | "hann" | "hamming";
  amplitude: "rms" | "amp";
}

export interface HarmonicAnalysis {
  frequencies: number[];
  amplitudes: number[];
  effectiveHarmonics: number;
  requestedHarmonics: number;
  nonFiniteSamples: number;
  config: FftConfig;
}

const windowWeight = (
  windowName: FftConfig["window"],
  index: number,
  count: number,
): number => {
  if (windowName === "rectangular" || count < 3) return 1;
  const phase = (2 * Math.PI * index) / (count - 1);
  if (windowName === "hann") return 0.5 - 0.5 * Math.cos(phase);
  return 0.54 - 0.46 * Math.cos(phase);
};

export const calculateHarmonics = (
  values: number[],
  config: FftConfig,
): HarmonicAnalysis => {
  const cleaned = new Float64Array(values.length);
  let rawSum = 0;
  let nonFiniteSamples = 0;
  values.forEach((rawValue, index) => {
    const value = Number.isFinite(rawValue) ? rawValue : 0;
    if (!Number.isFinite(rawValue)) nonFiniteSamples += 1;
    cleaned[index] = value;
    rawSum += value;
  });
  const dcAmplitude = values.length > 0 ? rawSum / values.length : 0;

  const weighted = new Float64Array(values.length);
  let windowSum = 0;
  cleaned.forEach((value, index) => {
    const weight = windowWeight(config.window, index, values.length);
    weighted[index] = (value - dcAmplitude) * weight;
    windowSum += weight;
  });
  if (windowSum <= Number.EPSILON) windowSum = values.length || 1;

  const nyquist = config.sample_frequency_hz / 2;
  const effectiveHarmonics = Math.max(
    0,
    Math.min(
      config.harmonic_count,
      Math.floor(nyquist / config.base_frequency_hz),
    ),
  );
  const frequencies = [0];
  const amplitudes = [dcAmplitude];

  for (let harmonic = 1; harmonic <= effectiveHarmonics; harmonic += 1) {
    const frequency = harmonic * config.base_frequency_hz;
    const omega =
      (2 * Math.PI * frequency) / config.sample_frequency_hz;
    const cosine = Math.cos(omega);
    const sine = Math.sin(omega);
    const coefficient = 2 * cosine;
    let previous = 0;
    let previous2 = 0;
    for (const sample of weighted) {
      const current = sample + coefficient * previous - previous2;
      previous2 = previous;
      previous = current;
    }
    const real = previous - previous2 * cosine;
    const imaginary = previous2 * sine;
    const oneSidedFactor =
      Math.abs(frequency - nyquist) < Number.EPSILON ? 1 : 2;
    let amplitude =
      (oneSidedFactor * Math.hypot(real, imaginary)) / windowSum;
    if (config.amplitude === "rms") amplitude /= Math.SQRT2;
    frequencies.push(frequency);
    amplitudes.push(amplitude);
  }

  return {
    frequencies,
    amplitudes,
    effectiveHarmonics,
    requestedHarmonics: config.harmonic_count,
    nonFiniteSamples,
    config,
  };
};
