import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { RunsListPage } from "./pages/RunsListPage";
import { RunDetailPage } from "./pages/RunDetailPage";

export function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<RunsListPage />} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
      </Routes>
    </Layout>
  );
}
