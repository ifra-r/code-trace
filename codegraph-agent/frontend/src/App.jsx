import { Routes, Route } from "react-router-dom";
import LandingPage from "./pages/LandingPage.jsx";
import QnAPage from "./pages/QnAPage.jsx";
import FlowPage from "./pages/FlowPage.jsx";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/qna" element={<QnAPage />} />
      <Route path="/flow" element={<FlowPage />} />
    </Routes>
  );
}