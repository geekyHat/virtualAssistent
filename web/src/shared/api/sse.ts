/**
 * Decoder SSE per lo stream di generazione (B-03.2-35; NewRay.md §18.4).
 *
 * Puro e senza dipendenze: riceve chunk di testo (già decodificati dal
 * `TextDecoder` del chiamante) ed emette frame completi `{ event, data }`.
 * Gestisce chunk di rete che spezzano righe, eventi e `data` multilinea.
 * Il terminale (es. `done`) arriva come frame come gli altri; è il
 * chiamante (reducer) a validare il terminale unico e a distinguere EOF
 * prematuro da `done`/`error`/abort.
 */

export interface SseFrame {
  /** Nome dell'evento (`event:`); `"message"` se assente. */
  event: string;
  /** Righe `data:` giunte; una sola riga nel contratto NewRay. */
  data: string;
}

/**
 * `push` accumula e emette i frame completi; `finish` chiude lo stream:
 * un frame lasciato a metà è scartato (il contratto NewRay emette frame
 * completi seguiti da `\n\n`).
 */
export class SseDecoder {
  private buffer = "";
  private pendingCR = false;

  push(chunk: string): SseFrame[] {
    // La spec SSE tratta \r\n, \r e \n come separatore di riga equivalente.
    // Un CR alla fine del chunk potrebbe essere seguito dal LF nel chunk
    // successivo: la coppia deve restare un solo separatore.
    if (this.pendingCR) {
      this.buffer += "\n";
      this.pendingCR = false;
      if (chunk.startsWith("\n")) chunk = chunk.slice(1);
    }
    if (chunk.endsWith("\r")) {
      this.pendingCR = true;
      chunk = chunk.slice(0, -1);
    }
    this.buffer += chunk.replace(/\r\n?/g, "\n");
    const frames: SseFrame[] = [];
    let start = 0;
    while (true) {
      const idx = this.buffer.indexOf("\n\n", start);
      if (idx === -1) break;
      const block = this.buffer.slice(start, idx);
      start = idx + 2;
      const frame = parseBlock(block);
      if (frame) frames.push(frame);
    }
    this.buffer = this.buffer.slice(start);
    return frames;
  }

  finish(): SseFrame[] {
    // Spec: un messaggio viene dispatchato solo alla riga vuota. Un frame
    // senza terminatore è incompleto e va scartato; il chiamante segnala
    // l'assenza del terminale come EOF (guasto), non come completion.
    const frames = this.pendingCR ? this.push("") : [];
    this.buffer = "";
    return frames;
  }
}

function parseBlock(block: string): SseFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    // `retry:` e i commenti (`:`) non sono usati dal contratto NewRay.
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}
