function setupPlaceFields(options) {
  const country = document.getElementById("country");
  const state = document.getElementById("state");
  const city = document.getElementById("city");
  const district = document.getElementById("district");
  const list = document.getElementById("district-list");
  const note = document.getElementById("place-note");
  let timer = null;
  let loadingCities = false;

  if (country) {
    country.value = "United States";
    country.disabled = true;
  }

  function setNote(message) {
    if (note) note.textContent = message || "";
  }

  function fillList(districts) {
    if (!list) return;
    list.innerHTML = "";
    (districts || []).forEach((item) => {
      const option = document.createElement("option");
      option.value = item.name;
      const sameCity = item.city && city && item.city.toLowerCase() === city.value.trim().toLowerCase();
      option.textContent = item.city && !sameCity ? `${item.name} — ${item.city}` : item.name;
      list.appendChild(option);
    });
    list.classList.toggle("hidden", list.options.length === 0);
  }

  function fillSelect(select, values, placeholder, selected) {
    if (!select) return;
    const keep = selected || select.value || "";
    select.innerHTML = "";
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = placeholder;
    select.appendChild(blank);
    (values || []).forEach((value) => {
      const option = document.createElement("option");
      if (typeof value === "string") {
        option.value = value;
        option.textContent = value;
      } else {
        option.value = value.name;
        option.textContent = value.name;
        if (value.code) option.dataset.code = value.code;
      }
      select.appendChild(option);
    });
    if (keep && Array.from(select.options).some((opt) => opt.value === keep)) {
      select.value = keep;
    } else {
      select.value = "";
    }
  }

  async function loadStates(preferred) {
    try {
      const response = await fetch("/api/places/states");
      const data = await response.json();
      fillSelect(state, data.states || [], "Select a state", preferred);
    } catch (err) {
      setNote("Could not load states. Refresh and try again.");
    }
  }

  async function loadCities(preferred) {
    if (!state || !state.value) {
      fillSelect(city, [], "Select a city first", "");
      fillList([]);
      return;
    }
    loadingCities = true;
    try {
      const params = new URLSearchParams({ state: state.value });
      const response = await fetch(`/api/places/cities?${params}`);
      const data = await response.json();
      fillSelect(city, data.cities || [], "Select a city", preferred);
      if ((data.cities || []).length === 0) {
        setNote("No cities listed for that state yet.");
      }
    } catch (err) {
      fillSelect(city, [], "Select a city", "");
      setNote("Could not load cities. Refresh and try again.");
    } finally {
      loadingCities = false;
    }
  }

  async function lookupDistricts(filterByName) {
    if (!state || !state.value) {
      fillList([]);
      return;
    }
    const params = new URLSearchParams({
      country: "United States",
      state: state.value.trim(),
      city: city ? city.value.trim() : "",
      q: filterByName && district ? district.value.trim() : "",
    });
    try {
      const response = await fetch(`/api/districts?${params}`);
      const data = await response.json();
      fillList(data.districts || []);
      if ((data.districts || []).length === 0) {
        setNote("No districts match that place. District is optional — you can leave it blank or type one.");
      } else {
        setNote("Pick a district from the list, type to filter, or leave it blank.");
      }
    } catch (err) {
      fillList([]);
      setNote("Could not load districts. District is optional.");
    }
  }

  function scheduleDistrictLookup(filterByName) {
    clearTimeout(timer);
    timer = setTimeout(() => lookupDistricts(filterByName), 250);
  }

  if (state) {
    state.addEventListener("change", async () => {
      await loadCities("");
      if (district) district.value = "";
      fillList([]);
      setNote("");
      scheduleDistrictLookup(false);
    });
  }
  if (city) {
    city.addEventListener("change", () => {
      if (loadingCities) return;
      if (district) district.value = "";
      scheduleDistrictLookup(false);
    });
  }
  if (district) {
    district.addEventListener("focus", () => scheduleDistrictLookup(!!district.value.trim()));
    district.addEventListener("input", () => scheduleDistrictLookup(true));
  }
  if (list) {
    list.addEventListener("change", () => {
      if (list.value && district) district.value = list.value;
    });
  }

  const initial = (options && options.initial) || {};
  return loadStates(initial.state || "").then(() => loadCities(initial.city || "")).then(() => {
    if (district && initial.district) district.value = initial.district;
    if (initial.state) scheduleDistrictLookup(false);
  });
}
