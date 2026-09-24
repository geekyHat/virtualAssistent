import { describe, expect, it } from "vitest";
import { SseDecoder } from "./sse";

describe("SseDecoder", () => {
  it("emette un frame completo da un solo chunk", () => {
    const decoder = new SseDecoder();
    const frames = decoder.push('event: delta\ndata: {"text":"ciao"}\n\n');
    expect(frames).toEqual([{ event: "delta", data: '{"text":"ciao"}', id: null }]);
  });

  it("emette più frame separati da \\n\\n nello stesso chunk", () => {
    const decoder = new SseDecoder();
    const frames = decoder.push(
      'event: delta\ndata: {"text":"a"}\n\nevent: delta\ndata: {"text":"b"}\n\n'
    );
    expect(frames).toHaveLength(2);
    expect(frames[0].data).toBe('{"text":"a"}');
    expect(frames[1].data).toBe('{"text":"b"}');
  });

  it("ricompone un frame spezzato su più chunk di rete", () => {
    const decoder = new SseDecoder();
    expect(decoder.push("event: del")).toEqual([]);
    expect(decoder.push('ta\ndata: {"text":"')).toEqual([]);
    const frames = decoder.push('parziale"}\n\n');
    expect(frames).toEqual([{ event: "delta", data: '{"text":"parziale"}', id: null }]);
  });

  it("ricompone una riga data spezzata a metà di un chunk", () => {
    const decoder = new SseDecoder();
    expect(decoder.push("data: {")).toEqual([]);
    const frames = decoder.push('"text":"x"}\n\n');
    expect(frames).toEqual([{ event: "message", data: '{"text":"x"}', id: null }]);
  });

  it("giunta le righe data multilinea con \\n", () => {
    const decoder = new SseDecoder();
    const frames = decoder.push("data: prima\ndata: seconda\n\n");
    expect(frames).toEqual([{ event: "message", data: "prima\nseconda", id: null }]);
  });

  it("default event a message quando la riga event è assente", () => {
    const decoder = new SseDecoder();
    const frames = decoder.push('data: {"ok":true}\n\n');
    expect(frames[0].event).toBe("message");
  });

  it("ignora commenti e righe retry", () => {
    const decoder = new SseDecoder();
    const frames = decoder.push(": commento\nretry: 3000\nevent: done\ndata: {}\n\n");
    expect(frames).toEqual([{ event: "done", data: "{}", id: null }]);
  });

  it("finish() non emette un frame a metà e non duplica i frame già emessi", () => {
    const decoder = new SseDecoder();
    decoder.push('event: delta\ndata: {"text":"a"}\n\n');
    decoder.push('event: delta\ndata: {"text":"a');
    expect(decoder.finish()).toEqual([]);
  });

  it("finish() scarta un frame completo ma non terminato (EOF senza terminale = guasto)", () => {
    const decoder = new SseDecoder();
    decoder.push('event: done\ndata: {"finish_reason":"stop"}');
    expect(decoder.finish()).toEqual([]);
  });

  it("gestisce CRLF come separatore di riga", () => {
    const decoder = new SseDecoder();
    const frames = decoder.push('event: delta\r\ndata: {"text":"x"}\r\n\r\n');
    expect(frames.length).toBe(1);
    expect(frames[0].event).toBe("delta");
  });

  it("produce lo stesso frame per ogni punto di split di un blocco CRLF", () => {
    const body = 'event: delta\r\ndata: {"text":"x"}\r\n\r\n';
    for (let split = 0; split <= body.length; split += 1) {
      const decoder = new SseDecoder();
      const frames = [
        ...decoder.push(body.slice(0, split)),
        ...decoder.push(body.slice(split)),
        ...decoder.finish(),
      ];
      expect(frames, `split=${split}`).toEqual([
        { event: "delta", data: '{"text":"x"}', id: null },
      ]);
    }
  });

  it("mantiene un solo separatore quando CRLF è diviso fra chunk", () => {
    const decoder = new SseDecoder();
    expect(decoder.push("event: done\r")).toEqual([]);
    expect(decoder.push('\ndata: {"finish_reason":"stop"}\r')).toEqual([]);
    expect(decoder.push("\n\r")).toEqual([]);
    expect(decoder.push("\n")).toEqual([
      { event: "done", data: '{"finish_reason":"stop"}', id: null },
    ]);
  });

  it("legge il campo id: come cursore intero (P-06)", () => {
    const decoder = new SseDecoder();
    const frames = decoder.push('id: 12\nevent: message.delta\ndata: {"text":"a"}\n\n');
    expect(frames).toEqual([{ event: "message.delta", data: '{"text":"a"}', id: 12 }]);
  });

  it("id: non numerico o assente resta null, non solleva", () => {
    const decoder = new SseDecoder();
    const frames = decoder.push("id: not-a-number\nevent: message.delta\ndata: {}\n\n");
    expect(frames[0].id).toBeNull();
  });
});
