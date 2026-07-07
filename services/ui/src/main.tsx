import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";
import { bootstrapTheme } from "./hooks/useTheme";

// Apply saved theme BEFORE first render to avoid a flash of the wrong palette.
bootstrapTheme();

// Inject the design-system icon sprite once so <use href="#td-i-..."> resolves
// anywhere in the app. It's hidden (display:none on the root <svg>), so it
// adds no layout — only the symbol definitions.
fetch("/td/icons/icons.svg")
  .then((r) => r.text())
  .then((svg) => {
    const div = document.createElement("div");
    div.innerHTML = svg;
    div.style.display = "none";
    document.body.insertBefore(div, document.body.firstChild);
  })
  .catch(() => {
    /* sprite unavailable — icons render empty; non-fatal */
  });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
