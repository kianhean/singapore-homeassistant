(() => {
  const REPO_URL = "https://github.com/kianhean/singapore-homeassistant";
  const DOCS_ENTITIES = `${REPO_URL}/blob/main/docs/entities.md`;

  // 1. Theme toggle
  const root = document.documentElement;
  const themeBtn = document.getElementById("theme-toggle");
  const themeLabel = document.getElementById("theme-label");

  const getPreferredTheme = () =>
    window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";

  const applyTheme = (theme) => {
    if (theme) {
      root.setAttribute("data-theme", theme);
    } else {
      root.removeAttribute("data-theme");
    }
    const active = theme || getPreferredTheme();
    if (themeLabel) {
      themeLabel.textContent = active === "dark" ? "Light" : "Dark";
    }
    if (themeBtn) {
      themeBtn.setAttribute(
        "aria-label",
        active === "dark" ? "Switch to light theme" : "Switch to dark theme"
      );
    }
  };

  const savedTheme = localStorage.getItem("sgha-theme");
  applyTheme(savedTheme);

  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const current = root.getAttribute("data-theme") || getPreferredTheme();
      const next = current === "dark" ? "light" : "dark";
      localStorage.setItem("sgha-theme", next);
      applyTheme(next);
    });
  }

  // 2. Data Source Explorer
  const DATA_SOURCES = [
    {
      name: "SP Group e-account",
      provider: "SP Group app APIs",
      category: "energy",
      tier: "sp",
      refresh: "Every 30 min",
      anchor: "sp-group-e-account-optional",
      desc: "Your own electricity (half-hourly on a smart meter), water, gas, bills, standby power, similar-home benchmarks, Green Goals and Energy dashboard history.",
    },
    {
      name: "SP Group utility tariffs",
      provider: "SP Group",
      category: "energy",
      tier: "default",
      refresh: "Every 24 h",
      anchor: "sp-group-utility-tariffs",
      desc: "Quarterly residential electricity, piped gas and water tariffs (incl. GST), plus your solar export price.",
    },
    {
      name: "NEA weather & forecasts",
      provider: "data.gov.sg / NEA",
      category: "weather",
      tier: "default",
      refresh: "Every 10 min",
      anchor: "weather-entities-by-forecast-area",
      desc: "Weather entity for every NEA forecast area with 2-hour, 24-hour regional and 4-day outlooks plus nearest-station temperature, humidity, wind and rain.",
    },
    {
      name: "Rain radar & animated loops",
      provider: "data.gov.sg / NEA",
      category: "weather",
      tier: "default",
      refresh: "Every 5 min",
      anchor: "rain-radar",
      desc: "70 km and 240 km rain radar stills and 1-hour animated loops drawn on NEA's official base maps.",
    },
    {
      name: "Air quality (PSI & PM2.5)",
      provider: "data.gov.sg / NEA",
      category: "weather",
      tier: "default",
      refresh: "Every 15 min",
      anchor: "air-quality-psi",
      desc: "24-hour PSI, hourly PM2.5 and six pollutants overall and by region, with coordinates ready for a Map card.",
    },
    {
      name: "UV index",
      provider: "data.gov.sg / NEA",
      category: "weather",
      tier: "default",
      refresh: "Every 15 min",
      anchor: "uv-index",
      desc: "NEA hourly UV index and exposure band (low to extreme) with today's peak.",
    },
    {
      name: "Heat stress (WBGT)",
      provider: "data.gov.sg / NEA",
      category: "weather",
      tier: "default",
      refresh: "Every 15 min",
      anchor: "heat-stress-wbgt",
      desc: "Wet Bulb Globe Temperature and heat stress level (low, moderate, high) from the station nearest your home.",
    },
    {
      name: "Weather warnings & advisories",
      provider: "NEA",
      category: "alerts",
      tier: "default",
      refresh: "Every 5 min",
      anchor: "nea-weather-warnings-and-advisories",
      desc: "Heavy rain warning binary sensor, active NEA advisories count and latest regional earthquake magnitude.",
    },
    {
      name: "Tide timings",
      provider: "NEA",
      category: "weather",
      tier: "default",
      refresh: "Every 24 h",
      anchor: "nea-tide-timings",
      desc: "Next high or low tide timestamp and predicted tide height.",
    },
    {
      name: "Lightning observations",
      provider: "data.gov.sg / NEA",
      category: "alerts",
      tier: "default",
      refresh: "Every 2 min",
      anchor: "lightning",
      desc: "Live lightning strikes, distance to the nearest strike from your home and a nearby binary sensor.",
    },
    {
      name: "PUB flash-flood alerts",
      provider: "data.gov.sg / PUB",
      category: "alerts",
      tier: "default",
      refresh: "Every 2 min",
      anchor: "pub-flood-alerts",
      desc: "Active PUB flash-flood alerts, affected locations and a nearby flood alert binary sensor.",
    },
    {
      name: "Dengue clusters",
      provider: "data.gov.sg / NEA",
      category: "alerts",
      tier: "default",
      refresh: "Every 6 h",
      anchor: "dengue-clusters",
      desc: "Active NEA dengue clusters and distance to the nearest cluster from your home location.",
    },
    {
      name: "MRT/LRT train status",
      provider: "mytransport.sg / LTA",
      category: "transport",
      tier: "default",
      refresh: "Every 5 min",
      anchor: "mrtlrt-train-status",
      desc: "Network-wide and per-line status (normal, planned works, disruption) with LTA's notice text and alert events.",
    },
    {
      name: "LTA traffic incidents",
      provider: "mytransport.sg / LTA",
      category: "transport",
      tier: "default",
      refresh: "Every 5 min",
      anchor: "lta-traffic-incidents",
      desc: "Live island-wide incident count, breakdown by type, nearest incident distance and nearby binary sensor.",
    },
    {
      name: "Checkpoint traffic cameras",
      provider: "data.gov.sg / LTA",
      category: "transport",
      tier: "default",
      refresh: "Every 2 min",
      anchor: "checkpoint-traffic-cameras",
      desc: "Live LTA camera stills for Woodlands Causeway, Checkpoint, Flyover, Tuas Second Link, Checkpoint, West Road and Sentosa Gateway.",
    },
    {
      name: "COE bidding results",
      provider: "data.gov.sg / LTA",
      category: "transport",
      tier: "default",
      refresh: "Daily at 19:30",
      anchor: "coe-bidding-results",
      desc: "Category A–E premiums and an event entity fired after each bidding exercise.",
    },
    {
      name: "HDB carpark availability",
      provider: "data.gov.sg / HDB",
      category: "transport",
      tier: "optin",
      refresh: "Every 5 min",
      anchor: "hdb-carpark-availability",
      desc: "Free lots at the HDB carparks you choose, or automatically the three nearest your home.",
    },
    {
      name: "CapitaLand mall carparks",
      provider: "CapitaLand JustPark",
      category: "transport",
      tier: "optin",
      refresh: "Every 5 min",
      anchor: "capitaland-mall-carparks",
      desc: "Live free lots at Plaza Singapura, Tampines Mall, Bedok Mall, IMM, Westgate and other CapitaLand malls.",
    },
    {
      name: "Taxi availability",
      provider: "data.gov.sg / LTA",
      category: "transport",
      tier: "optin",
      refresh: "Every 5 min",
      anchor: "taxi-availability",
      desc: "Available taxis within 500 m, 1 km and 2 km of home, island-wide total and Map card markers.",
    },
    {
      name: "Pump prices (Caltex, SPC, Shell)",
      provider: "Caltex · SPC · Shell",
      category: "transport",
      tier: "optin",
      refresh: "Every 6 h",
      anchor: "pump-prices",
      desc: "92, 95, 98, premium and diesel prices directly from each brand's website, including discounted prices.",
    },
    {
      name: "Mall carparks (LTA DataMall)",
      provider: "LTA DataMall",
      category: "transport",
      tier: "lta",
      refresh: "Every 5 min",
      anchor: "mall-carparks-lta-datamall",
      desc: "Free lots at ION Orchard, VivoCity, Suntec City, Jem and other malls on LTA's carpark signs.",
    },
    {
      name: "MRT/LRT crowd density",
      provider: "LTA DataMall",
      category: "transport",
      tier: "lta",
      refresh: "Every 10 min",
      anchor: "mrtlrt-crowd-density-and-ev-charger-availability-optional-lta-datamall-key",
      desc: "Real-time platform crowd level (low, moderate, high) for the stations you pick.",
    },
    {
      name: "EV charger availability",
      provider: "LTA DataMall",
      category: "transport",
      tier: "lta",
      refresh: "Every 5 min",
      anchor: "mrtlrt-crowd-density-and-ev-charger-availability-optional-lta-datamall-key",
      desc: "Available EV charging points by postal code or within 2 km of your home.",
    },
    {
      name: "Public holidays",
      provider: "MOM",
      category: "daily",
      tier: "default",
      refresh: "Every 24 h",
      anchor: "public-holidays",
      desc: "Official Singapore public holidays as a calendar, next holiday sensor and public-holiday-today binary sensor.",
    },
    {
      name: "MOE school calendar",
      provider: "MOE",
      category: "daily",
      tier: "default",
      refresh: "Every 24 h",
      anchor: "moe-school-calendar",
      desc: "School holidays, school terms and national exams as calendars plus a school-holiday-today binary sensor.",
    },
    {
      name: "Hawker centre closures",
      provider: "data.gov.sg / NEA",
      category: "daily",
      tier: "default",
      refresh: "Every 24 h",
      anchor: "hawker-centre-closures",
      desc: "Spring cleaning and repair closure dates for the five hawker centres nearest your home, as a calendar.",
    },
    {
      name: "Beach water quality",
      provider: "NEA",
      category: "daily",
      tier: "default",
      refresh: "Every 6 h",
      anchor: "beach-water-quality",
      desc: "Weekly water quality band and NEA swimming advice for East Coast, Sentosa, Changi, Pasir Ris, Punggol, Seletar and Sembawang.",
    },
    {
      name: "Prayer times (MUIS)",
      provider: "data.gov.sg / MUIS",
      category: "daily",
      tier: "optin",
      refresh: "Every 12 h",
      anchor: "prayer-times",
      desc: "Official MUIS timetable: today's six prayer times and the next prayer as timestamp sensors for automations.",
    },
    {
      name: "FX rates (SGD crosses)",
      provider: "ECB",
      category: "daily",
      tier: "default",
      refresh: "Every 4 h",
      anchor: "fx-rates-and-interest-rates",
      desc: "Reference exchange rates against SGD (USD, EUR, GBP, JPY, MYR, CNY, AUD, HKD by default; customisable).",
    },
    {
      name: "SORA, T-bill & SGS yields",
      provider: "SingStat / MAS",
      category: "daily",
      tier: "default",
      refresh: "Every 12 h",
      anchor: "fx-rates-and-interest-rates",
      desc: "SORA, compounded SORA, 6-month T-bill and SGS bond yields straight from MAS / SingStat.",
    },
  ];

  const TIER_META = {
    default: { label: "Default", cls: "badge-default" },
    optin: { label: "Opt-in", cls: "badge-optin" },
    lta: { label: "Opt-in · LTA key", cls: "badge-lta" },
    sp: { label: "Optional · SP", cls: "badge-sp" },
  };

  let activeCategory = "all";
  let activeTier = "all";
  let searchQuery = "";

  const gridEl = document.getElementById("sources-grid");
  const countEl = document.getElementById("sources-count");
  const searchEl = document.getElementById("sources-search");

  const renderSources = () => {
    if (!gridEl) return;
    const q = searchQuery.trim().toLowerCase();
    const filtered = DATA_SOURCES.filter((item) => {
      if (activeCategory !== "all" && item.category !== activeCategory) return false;
      if (activeTier !== "all" && item.tier !== activeTier) return false;
      if (q) {
        const hay = `${item.name} ${item.provider} ${item.desc}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });

    if (countEl) {
      countEl.textContent = `Showing ${filtered.length} of ${DATA_SOURCES.length} data sources`;
    }

    if (filtered.length === 0) {
      gridEl.innerHTML = `<div class="empty-state">No data sources match your filter. Try clearing the search or selecting <strong>All</strong>.</div>`;
      return;
    }

    gridEl.innerHTML = filtered
      .map((item) => {
        const meta = TIER_META[item.tier];
        return `
          <article class="source-card">
            <div class="source-head">
              <h3 class="source-title">${item.name}</h3>
              <span class="badge ${meta.cls}">${meta.label}</span>
            </div>
            <p class="source-desc">${item.desc}</p>
            <div class="source-foot">
              <span>${item.provider} · ${item.refresh}</span>
              <a href="${DOCS_ENTITIES}#${item.anchor}" target="_blank" rel="noopener">Entities →</a>
            </div>
          </article>
        `;
      })
      .join("");
  };

  document.querySelectorAll("[data-category]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-category]").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      activeCategory = btn.getAttribute("data-category");
      renderSources();
    });
  });

  document.querySelectorAll("[data-tier]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-tier]").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
      activeTier = btn.getAttribute("data-tier");
      renderSources();
    });
  });

  if (searchEl) {
    searchEl.addEventListener("input", (e) => {
      searchQuery = e.target.value;
      renderSources();
    });
  }

  renderSources();

  // 3. Dashboard Gallery Tabs
  const galleryTabs = document.querySelectorAll("[data-gallery-tab]");
  const galleryPanels = document.querySelectorAll("[data-gallery-panel]");

  galleryTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const target = tab.getAttribute("data-gallery-tab");
      galleryTabs.forEach((t) => {
        const isCurrent = t === tab;
        t.classList.toggle("is-active", isCurrent);
        t.setAttribute("aria-selected", String(isCurrent));
      });
      galleryPanels.forEach((panel) => {
        panel.classList.toggle("is-active", panel.getAttribute("data-gallery-panel") === target);
      });
    });
  });

  // 4. Lightbox for Screenshots
  const lightbox = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightbox-img");
  const lightboxCaption = document.getElementById("lightbox-caption");
  const lightboxClose = document.getElementById("lightbox-close");

  const openLightbox = (src, alt, caption) => {
    if (!lightbox || !lightboxImg) return;
    lightboxImg.src = src;
    lightboxImg.alt = alt || "";
    if (lightboxCaption) lightboxCaption.textContent = caption || alt || "";
    lightbox.classList.add("is-open");
    document.body.style.overflow = "hidden";
  };

  const closeLightbox = () => {
    if (!lightbox) return;
    lightbox.classList.remove("is-open");
    document.body.style.overflow = "";
  };

  document.querySelectorAll("[data-lightbox]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const img = btn.querySelector("img");
      if (!img) return;
      openLightbox(img.src, img.alt, btn.getAttribute("data-caption"));
    });
  });

  if (lightboxClose) lightboxClose.addEventListener("click", closeLightbox);
  if (lightbox) {
    lightbox.addEventListener("click", (e) => {
      if (e.target === lightbox) closeLightbox();
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeLightbox();
  });

  // 5. Progressive enhancement: latest release tag from GitHub
  const versionEl = document.getElementById("latest-version");
  if (versionEl) {
    fetch("https://api.github.com/repos/kianhean/singapore-homeassistant/releases/latest")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data && data.tag_name) {
          versionEl.textContent = data.tag_name;
        }
      })
      .catch(() => {});
  }
})();
