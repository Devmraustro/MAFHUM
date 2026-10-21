import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Header } from "./header";
import { LocalesProvider } from "@/components/locale-provider";

afterEach(() => cleanup());

function renderHeader(locale: "ar" | "fr") {
  localStorage.setItem("mafhum-locale", locale);
  return render(
    <LocalesProvider>
      <Header />
    </LocalesProvider>,
  );
}

describe("Header", () => {
  it("renders the MAFHUM brand and Arabic navigation links", () => {
    renderHeader("ar");
    expect(screen.getByText("MAFHUM")).toBeInTheDocument();
    expect(screen.getByText("الرئيسية")).toBeInTheDocument();
    expect(screen.getByText("عن المنصة")).toBeInTheDocument();
    expect(screen.getByText("حالة المشروع")).toBeInTheDocument();
  });

  it("switches to French navigation links", () => {
    renderHeader("fr");
    expect(screen.getByText("Accueil")).toBeInTheDocument();
    expect(screen.getByText("À propos")).toBeInTheDocument();
    expect(screen.getByText("État du projet")).toBeInTheDocument();
    expect(screen.queryByText("الرئيسية")).not.toBeInTheDocument();
  });
});
