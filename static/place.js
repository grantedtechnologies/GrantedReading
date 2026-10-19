function setupPlaceFields(options) {
  const country = document.getElementById("country");
  const state = document.getElementById("state");
  const city = document.getElementById("city");
  const district = document.getElementById("district");
  const manual = document.getElementById("place-manual");
  const list = document.getElementById("district-list");
  const note = document.getElementById("place-note");
  let asked = false;
  let timer = null;

  function showManual() {
    manual.hidden = false;
  }

  function setNote(message) {
    note.textContent = message || "";
  }

  function fillList(districts) {
    list.innerHTML = "";
    (districts || []).forEach((item) => {
      const option = document.createElement("option");
      option.value = item.name;
      const sameCity = item.city && item.city.toLowerCase() === city.value.trim().toLowerCase();
      option.textContent = item.city && !sameCity ? `${item.name} — ${item.city}` : item.name;
      list.appendChild(option);
    });
    list.classList.toggle("hidden", list.options.length === 0);
  }

  async function lookup(filterByName) {
    const place = {
      country: country.value.trim(),
      state: state.value.trim(),
      city: city.value.trim(),
    };
    if (!place.country || !place.state || !place.city) {
      fillList([]);
      return;
    }
    const params = new URLSearchParams({
      ...place,
      q: filterByName ? district.value.trim() : "",
    });
    try {
      const response = await fetch(`/api/districts?${params}`);
      const data = await response.json();
      fillList(data.districts || []);
      if ((data.districts || []).length === 0) {
        setNote("No districts match that place. You can type your district.");
      } else {
        setNote("");
      }
    } catch (err) {
      fillList([]);
      setNote("Could not load districts. You can type your district.");
    }
  }

  function scheduleLookup(filterByName) {
    clearTimeout(timer);
    timer = setTimeout(() => lookup(filterByName), 250);
  }

  async function useLocation() {
    showManual();
    if (!navigator.geolocation) {
      setNote("This browser cannot share location. Enter your country, state, and city.");
      return;
    }
    setNote("Finding nearby districts…");
    navigator.geolocation.getCurrentPosition(async (position) => {
      try {
        const response = await fetch("/api/districts/nearby", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
          }),
        });
        const data = await response.json();
        if (!response.ok) {
          setNote(data.error || "Could not look up that location.");
          return;
        }
        if (data.country) country.value = data.country;
        if (data.state) state.value = data.state;
        if (data.city) city.value = data.city;
        fillList(data.districts || []);
        if (data.districts && data.districts.length) {
          district.value = data.districts[0].name;
          setNote("Nearby districts are listed. Pick one or type your own.");
        } else {
          setNote("No districts are close to that spot. Enter your country, state, and city, or type the district.");
        }
      } catch (err) {
        setNote("Could not look up that location. Enter your country, state, and city.");
      }
    }, () => {
      setNote("Location was blocked. Enter your country, state, and city.");
    });
  }

  function askForLocation() {
    if (asked) return;
    asked = true;
    useLocation();
  }

  district.addEventListener("focus", askForLocation);
  district.addEventListener("click", askForLocation);
  list.addEventListener("change", () => {
    if (list.value) district.value = list.value;
  });
  country.addEventListener("input", () => scheduleLookup(false));
  state.addEventListener("input", () => scheduleLookup(false));
  city.addEventListener("input", () => scheduleLookup(false));
  district.addEventListener("input", () => scheduleLookup(true));

  if (!options || !options.hideUntilChoice) {
    showManual();
  }

  return { refresh: lookup };
}
