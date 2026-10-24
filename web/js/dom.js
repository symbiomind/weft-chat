/* Weft — the element helper.
 *
 * This function had three identical copies (login, change-password, chat) before the
 * settings screens would have made it five. It is small enough that duplicating it
 * felt harmless, which is exactly how a helper ends up with five slightly different
 * versions and a bug that only reproduces on one screen.
 *
 * The important property is the one it has by construction: there is no way to pass
 * markup through it. `text` sets textContent, attributes go through setAttribute, and
 * nothing anywhere takes innerHTML. Every string this app renders came off the wire —
 * a provider name someone typed, a model id from a third-party gateway — and none of
 * it is ever parsed as HTML.
 */

export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

/**
 * A row that is itself the control: a <div role="button"> that Enter and Space press.
 *
 * Not a <button>, because Chromium lays a button's children out inside an anonymous box
 * of its own, so a column inside it set to `flex:1` does not grow and the row's content
 * sits centred however the CSS asks. A div takes the row layout as written.
 */
export function rowButton(props = {}, children = []) {
  const { onclick, ...rest } = props;
  const node = el("div", { role: "button", tabindex: "0", ...rest }, children);
  node.addEventListener("click", onclick);
  node.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onclick(event);
  });
  return node;
}
