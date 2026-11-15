import {StrictMode} from "react";
import {createRoot} from "react-dom/client";
import {App} from "./App";
import "@/design-system/base.css";
import "./foundation-gallery.css";

const root = document.querySelector("#root");
if (!root) throw new Error("gallery_root_missing");
createRoot(root).render(<StrictMode><App /></StrictMode>);
