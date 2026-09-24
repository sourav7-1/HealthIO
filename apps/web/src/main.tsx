import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { applyLargeText } from "./features/patient/largeText";
import { registerServiceWorker } from "./lib/push";
import "./styles.css";

applyLargeText();
// Only needed to show push reminders; the app works the same without it.
void registerServiceWorker();

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
