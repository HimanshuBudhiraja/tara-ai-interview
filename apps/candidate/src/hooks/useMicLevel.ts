import { useCallback, useEffect, useRef, useState } from "react";

export type MicStatus = "idle" | "requesting" | "ready" | "denied" | "unavailable";

/**
 * Live input level from the microphone, 0–1.
 *
 * Its real job is reassurance: a candidate about to be interviewed by software
 * needs to see the bar move before they'll trust the thing to hear them. It is
 * never sent anywhere and never recorded — the analyser reads the stream in the
 * browser and nothing leaves the page.
 */
export function useMicLevel() {
  const [status, setStatus] = useState<MicStatus>("idle");
  const [level, setLevel] = useState(0);
  const [heardSomething, setHeardSomething] = useState(false);

  const stream = useRef<MediaStream | null>(null);
  const context = useRef<AudioContext | null>(null);
  const frame = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    void context.current?.close();
    context.current = null;
    setLevel(0);
  }, []);

  const request = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("unavailable");
      return false;
    }
    setStatus("requesting");
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      stream.current = media;

      const ctx = new AudioContext();
      context.current = ctx;
      const source = ctx.createMediaStreamSource(media);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);

      const buffer = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteTimeDomainData(buffer);
        // RMS around the 128 midpoint, scaled so ordinary speech lands near 0.6.
        let sum = 0;
        for (let i = 0; i < buffer.length; i += 1) {
          const v = (buffer[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buffer.length);
        const scaled = Math.min(1, rms * 4.5);
        setLevel(scaled);
        if (scaled > 0.12) setHeardSomething(true);
        frame.current = requestAnimationFrame(tick);
      };
      tick();

      setStatus("ready");
      return true;
    } catch (err: unknown) {
      const name = (err as { name?: string })?.name;
      setStatus(name === "NotAllowedError" || name === "SecurityError" ? "denied" : "unavailable");
      return false;
    }
  }, []);

  useEffect(() => stop, [stop]);

  return { status, level, heardSomething, request, stop };
}
