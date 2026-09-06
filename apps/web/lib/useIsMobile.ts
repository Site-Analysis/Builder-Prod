"use client";

import { useEffect, useState } from "react";

export function useIsMobile() {
  const [width, setWidth] = useState(1024); // SSR-safe default (renders as desktop)
  useEffect(() => {
    const update = () => setWidth(window.innerWidth);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return { isMobile: width < 640, isTablet: width < 1024 };
}
