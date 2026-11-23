import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/libre-baskerville/400.css";
import "@fontsource/libre-baskerville/400-italic.css";
import "@fontsource/source-serif-4/400.css";
import "@fontsource/source-serif-4/400-italic.css";
import "@fontsource/source-serif-4/600.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";
import { ThemeProvider } from "./theme";
import { MarketProvider } from "./market";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <MarketProvider>
        <App />
      </MarketProvider>
    </ThemeProvider>
  </StrictMode>,
);
