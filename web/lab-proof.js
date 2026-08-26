"use strict";

(function exposeProofGrid(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root && root.document) root.FontBlindProof = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const MAX_RENDERED_AXES = 2;

  function finiteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function normalizedAxis(axis, index = 0) {
    if (!axis || typeof axis !== "object" || Array.isArray(axis) ||
        typeof axis.tag !== "string" || !/^[A-Za-z0-9]{4}$/.test(axis.tag) ||
        typeof axis.name !== "string" || !axis.name.trim() ||
        !finiteNumber(axis.min) || !finiteNumber(axis.default) || !finiteNumber(axis.max) ||
        axis.min > axis.default || axis.default > axis.max) {
      throw new TypeError(`Invalid designspace axis at index ${index}`);
    }
    return {
      tag: axis.tag,
      name: axis.name,
      min: axis.min,
      default: axis.default,
      max: axis.max
    };
  }

  function addRole(points, value, role) {
    const existing = points.find((point) => Object.is(point.value, value));
    if (existing) {
      if (!existing.roles.includes(role)) existing.roles.push(role);
      return;
    }
    points.push({ value, roles: [role] });
  }

  function axisSamples(axis) {
    const spec = normalizedAxis(axis);
    const points = [];
    addRole(points, spec.min, "min");
    addRole(points, spec.default, "default");
    addRole(points, spec.max, "max");

    if (points.length < 3 && spec.min < spec.max) {
      addRole(points, spec.min + ((spec.max - spec.min) / 2), "mid");
    }
    points.sort((left, right) => left.value - right.value);
    return points.map((point) => ({
      value: point.value,
      roles: [...point.roles]
    }));
  }

  function tolerance(axis) {
    return Math.max(0.0001, Math.abs(axis.max - axis.min) / 10000);
  }

  function sameLocation(left, right, axes) {
    return axes.every((axis) =>
      finiteNumber(left[axis.tag]) && finiteNumber(right[axis.tag]) &&
      Math.abs(left[axis.tag] - right[axis.tag]) <= tolerance(axis)
    );
  }

  function normalizedMasters(masters, axes) {
    if (!Array.isArray(masters)) throw new TypeError("Invalid anonymous master list");
    const ids = new Set();
    let defaults = 0;
    return masters.map((master, index) => {
      if (!master || typeof master !== "object" || Array.isArray(master) ||
          typeof master.id !== "string" || !/^M\d{2}$/.test(master.id) || ids.has(master.id) ||
          typeof master.default !== "boolean" || !master.location ||
          typeof master.location !== "object" || Array.isArray(master.location)) {
        throw new TypeError(`Invalid anonymous master at index ${index}`);
      }
      ids.add(master.id);
      defaults += master.default ? 1 : 0;
      const location = {};
      for (const axis of axes) {
        const value = master.location[axis.tag];
        if (!finiteNumber(value) || value < axis.min || value > axis.max) {
          throw new TypeError(`Invalid ${axis.tag} coordinate for ${master.id}`);
        }
        location[axis.tag] = value;
      }
      if (Object.keys(master.location).length !== axes.length) {
        throw new TypeError(`Incomplete anonymous master ${master.id}`);
      }
      return { id: master.id, default: master.default, location };
    }).map((master, _index, all) => {
      if (all.length && defaults !== 1) {
        throw new TypeError("Anonymous master map needs one default");
      }
      return master;
    });
  }

  function pointFromSamples(samples, axes, masters, index) {
    const location = {};
    const roles = [];
    samples.forEach((sample, axisIndex) => {
      const axis = axes[axisIndex];
      location[axis.tag] = sample.value;
      roles.push({ tag: axis.tag, roles: [...sample.roles] });
    });
    const master = masters.find((candidate) => sameLocation(candidate.location, location, axes)) || null;
    const defaultLocation = Object.fromEntries(axes.map((axis) => [axis.tag, axis.default]));
    return {
      id: `P${String(index + 1).padStart(2, "0")}`,
      location,
      roles,
      masterId: master ? master.id : null,
      isMaster: Boolean(master),
      isDefault: sameLocation(location, defaultLocation, axes)
    };
  }

  function buildLocations(rawAxes, rawMasters = []) {
    if (!Array.isArray(rawAxes) || rawAxes.length < 1 || rawAxes.length > MAX_RENDERED_AXES) {
      throw new TypeError("Proof grid supports one or two axes");
    }
    const axes = rawAxes.map(normalizedAxis);
    if (new Set(axes.map((axis) => axis.tag)).size !== axes.length) {
      throw new TypeError("Proof grid axes must be unique");
    }
    const masters = normalizedMasters(rawMasters, axes);
    const samples = axes.map(axisSamples);
    const locations = [];

    if (axes.length === 1) {
      samples[0].forEach((xSample) => {
        locations.push(pointFromSamples([xSample], axes, masters, locations.length));
      });
    } else {
      [...samples[1]].reverse().forEach((ySample) => {
        samples[0].forEach((xSample) => {
          locations.push(pointFromSamples([xSample, ySample], axes, masters, locations.length));
        });
      });
    }
    return locations;
  }

  function formatNumber(value) {
    if (Number.isInteger(value)) return String(value);
    return String(Math.round(value * 100) / 100);
  }

  function settingsFor(location, rawAxes) {
    const axes = rawAxes.map(normalizedAxis);
    return axes.map((axis) => `"${axis.tag}" ${formatNumber(location[axis.tag])}`).join(", ");
  }

  function roleLabel(point) {
    return point.roles
      .map((item) => `${item.tag} ${item.roles.join(" / ")}`)
      .join(" · ")
      .toUpperCase();
  }

  function coordinateLabel(point, axes) {
    return axes.map((axis) => `${axis.tag} ${formatNumber(point.location[axis.tag])}`).join(" · ");
  }

  function badgeLabel(point) {
    const defaultCopy = point.isDefault ? " · DEFAULT" : "";
    return point.isMaster ? `MASTER ${point.masterId}${defaultCopy}` : `INTERPOLATION${defaultCopy}`;
  }

  function render(container, rawAxes, rawMasters = [], options = {}) {
    if (!container || typeof container.append !== "function") {
      throw new TypeError("Proof grid needs a DOM container");
    }
    const axes = rawAxes.map(normalizedAxis);
    const points = buildLocations(axes, rawMasters);
    const onSelect = typeof options.onSelect === "function" ? options.onSelect : () => {};
    const cards = new Map();

    const shell = document.createElement("section");
    shell.className = "designspace-proof-shell";
    shell.setAttribute("aria-label", "Deterministic designspace proof locations");

    const heading = document.createElement("div");
    heading.className = "designspace-proof-heading";
    const title = document.createElement("strong");
    title.textContent = "DESIGNSPACE PROOF GRID";
    const note = document.createElement("span");
    note.textContent = `${points.length} deterministic ${points.length === 1 ? "location" : "locations"} · exact masters marked`;
    heading.append(title, note);

    const grid = document.createElement("div");
    grid.className = `designspace-proof-grid is-${axes.length}d`;
    grid.style.setProperty("--proof-columns", String(axisSamples(axes[0]).length));

    for (const point of points) {
      const coordinates = coordinateLabel(point, axes);
      const badge = badgeLabel(point);
      const card = document.createElement("button");
      card.type = "button";
      card.className = `proof-point-card ${point.isMaster ? "is-master" : "is-interpolation"}${point.isDefault ? " is-default" : ""}`;
      card.setAttribute("aria-label", `${point.id}. ${badge}. ${coordinates}. Select this proof location.`);
      card.setAttribute("aria-pressed", "false");

      const meta = document.createElement("span");
      meta.className = "proof-point-meta";
      const pointId = document.createElement("code");
      pointId.textContent = point.id;
      const badgeNode = document.createElement("small");
      badgeNode.textContent = badge;
      meta.append(pointId, badgeNode);

      const specimen = document.createElement("span");
      specimen.className = "proof-point-specimen";
      specimen.setAttribute("aria-hidden", "true");
      specimen.textContent = "Hamburgefontsiv\nAVATAR 012345";
      specimen.style.fontVariationSettings = settingsFor(point.location, axes);
      if (typeof options.fontFamily === "string" && options.fontFamily) {
        specimen.style.fontFamily = options.fontFamily;
      }

      const coordinateNode = document.createElement("span");
      coordinateNode.className = "proof-point-coordinates";
      coordinateNode.textContent = coordinates;
      const roles = document.createElement("span");
      roles.className = "proof-point-roles";
      roles.textContent = roleLabel(point);

      card.append(meta, specimen, coordinateNode, roles);
      card.addEventListener("click", () => onSelect({ ...point.location }));
      cards.set(point.id, { card, point });
      grid.append(card);
    }

    shell.append(heading, grid);
    container.append(shell);

    function sync(values) {
      const current = values instanceof Map ? Object.fromEntries(values) : values;
      for (const { card, point } of cards.values()) {
        const active = current && sameLocation(current, point.location, axes);
        card.classList.toggle("is-active", Boolean(active));
        card.setAttribute("aria-pressed", active ? "true" : "false");
      }
    }

    sync(Object.fromEntries(axes.map((axis) => [axis.tag, axis.default])));
    return Object.freeze({ sync, points: points.map((point) => ({ ...point, location: { ...point.location } })) });
  }

  return Object.freeze({
    axisSamples,
    buildLocations,
    settingsFor,
    render
  });
});
