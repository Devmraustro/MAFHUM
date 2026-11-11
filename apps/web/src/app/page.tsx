import { HomeContent } from "./home-content";

/**
 * Public home page.
 *
 * This is a server component with no data fetching, so it renders as static
 * HTML: a visitor sees the product description immediately, before any
 * JavaScript runs. Signed-in students reach their workspace through the header
 * navigation, which is client-side and knows the session state.
 */
export default function HomePage() {
  return <HomeContent />;
}
