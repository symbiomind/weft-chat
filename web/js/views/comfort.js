/* Weft — the Comfort screen: how much moves, how much is see-through, which hand.
 *
 * Three rows of choices, each with a line saying what the one in use does. The rules
 * live in ../comfort.js; this only shows them. Per device, like the theme.
 *
 * WHAT THE DEVICE FORBIDS IS SHOWN, NOT HIDDEN. When the operating system asks for
 * reduced motion, Full is still on the row, switched off, with a line saying why. A
 * choice that silently does nothing reads as broken, and an option that vanished
 * leaves people wondering where it went.
 */

import * as comfort from "../comfort.js";
import { el } from "../dom.js";
import { PURPOSE, panelHead } from "../panels.js";

const MOTION = {
  full: { label: "Full", says: "Everything moves as designed: the ring breathes, replies stream in word by word." },
  calm: { label: "Calm", says: "Nothing large or looping moves. The ring holds still and changes colour; replies appear twice a second." },
  still: { label: "Still", says: "Nothing moves at all. The ring changes colour in place; replies appear a paragraph at a time." },
};

const GLASS = {
  glass: { label: "Glass", says: "Frosted surfaces, with the floor showing through." },
  less: { label: "Less", says: "Mostly solid surfaces and a light blur, for easier reading." },
  solid: { label: "Solid", says: "Fully solid surfaces and no blur anywhere." },
};

const HAND = {
  right: { label: "Right", says: "Send sits on the right, under a right thumb." },
  left: { label: "Left", says: "Send sits on the left, under a left thumb. The text box moves over." },
};

/** Why the device has capped a setting, in its own terms. Null when it has not. */
function motionReason(os) {
  if (os.forcedColors) return "Your device is in high contrast mode, so Full motion is off.";
  if (os.reducedMotion) return "Your device asks for reduced motion, so Full motion is off.";
  return null;
}

function glassReason(os) {
  if (os.forcedColors) return "Your device is in high contrast mode, so only Solid is on.";
  if (os.reducedTransparency) return "Your device asks for reduced transparency, so Glass is off.";
  if (os.moreContrast) return "Your device asks for more contrast, so Glass is off.";
  return null;
}

/**
 * One setting: a heading, a row of choices, what the chosen one does, and why any are
 * off. `ceiling` and `order` decide which are off; null for a setting with no ceiling.
 */
function settingRow({ title, name, options, order, ceiling, reason }) {
  const buttons = new Map();
  const says = el("p", { class: "hint comfort-says" });
  const why = el("p", { class: "comfort-why", hidden: "" });

  const group = el("div", { class: "seg comfort-seg", role: "group", "aria-label": title },
    Object.entries(options).map(([value, { label }]) => {
      const button = el("button", {
        type: "button", class: "seg-btn", "data-value": value, text: label,
        onclick: () => comfort.choose({ [name]: value }),
      });
      buttons.set(value, button);
      return button;
    }));

  function paint(state) {
    const now = state[name];
    for (const [value, button] of buttons) {
      button.setAttribute("aria-pressed", String(value === now));
      const off = ceiling ? comfort.aboveCeiling(value, state.ceiling[name], order) : false;
      button.disabled = off;
      button.title = off ? reason(state.os) ?? "" : "";
    }
    says.textContent = options[now].says;
    const cause = ceiling ? reason(state.os) : null;
    why.textContent = cause ?? "";
    why.hidden = !cause;
  }

  return {
    node: el("section", { class: "comfort-row" }, [el("h3", { text: title }), group, says, why]),
    paint,
  };
}

export function renderComfort(panels) {
  const rows = [
    settingRow({
      title: "MOTION", name: "motion", options: MOTION, order: comfort.MOTION,
      ceiling: true, reason: motionReason,
    }),
    settingRow({
      title: "TRANSPARENCY", name: "glass", options: GLASS, order: comfort.GLASS,
      ceiling: true, reason: glassReason,
    }),
    settingRow({
      title: "SEND BUTTON", name: "hand", options: HAND, order: comfort.HANDS,
      ceiling: false, reason: () => null,
    }),
  ];

  const paintAll = (state) => rows.forEach((row) => row.paint(state));
  paintAll(comfort.current());
  // The device can change while the screen is open (a phone's settings, then back).
  const unwatch = comfort.subscribe(paintAll);

  const body = el("div", { class: "panel-body comfort" }, [
    el("p", {
      class: "hint",
      text: "On this device only. Your device's own accessibility settings come first: "
        + "this screen can make Weft calmer than they ask, never busier.",
    }),
    ...rows.map((row) => row.node),
  ]);

  return {
    node: el("div", { class: "panel-inner" }, [
      panelHead(panels, { title: "Comfort", sub: PURPOSE.comfort }),
      body,
    ]),
    teardown: unwatch,
  };
}
