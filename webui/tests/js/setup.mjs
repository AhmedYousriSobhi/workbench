// The few browser globals the page's modules touch while they load (not while they run). Import this before any page module.
globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
globalThis.document = {
  createElement: () => ({ getContext: () => ({ measureText: s => ({ width: String(s).length * 7 }) }) }),
  getElementById: () => null,
  addEventListener() {},
  hidden: false,
};
