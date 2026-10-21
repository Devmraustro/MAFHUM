import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import Home from "./page";
import { LocalesProvider } from "@/components/locale-provider";

afterEach(() => cleanup());

function renderHome(locale: "ar" | "fr") {
  localStorage.setItem("mafhum-locale", locale);
  return render(
    <LocalesProvider>
      <Home />
    </LocalesProvider>,
  );
}

describe("Home page", () => {
  it("renders the Arabic hero title and honest under-construction status", () => {
    renderHome("ar");
    expect(screen.getByRole("heading", { level: 1, name: "MAFHUM" })).toBeInTheDocument();
    expect(screen.getByText("الحالة الحالية: تحت الإنشاء")).toBeInTheDocument();
    expect(screen.getByText(/هذه المرحلة توثق فقط/)).toBeInTheDocument();
  });

  it("renders French equivalents", () => {
    renderHome("fr");
    expect(screen.getByRole("heading", { level: 1, name: "MAFHUM" })).toBeInTheDocument();
    expect(screen.getByText("État actuel : en construction")).toBeInTheDocument();
    expect(screen.getByText(/Cette phase documente uniquement/)).toBeInTheDocument();
  });

  it("does not include fake feature claims or placeholder data", () => {
    renderHome("ar");
    const body = document.body.innerHTML;
    expect(body).not.toContain("بيانات تجريبية");
    expect(body).not.toMatch(/lorem/i);
  });
});
