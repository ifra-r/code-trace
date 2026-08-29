import { Routes, Route } from "react-router-dom";
import LandingPage from "./pages/LandingPage.jsx";
import QnAPage from "./pages/QnAPage.jsx";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/qna" element={<QnAPage />} />
    </Routes>
  );
}