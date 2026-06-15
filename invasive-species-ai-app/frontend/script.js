const API_URL = "http://localhost:8000";
      const map = L.map("map").setView([39.3999, -8.2245], 6);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "© OpenStreetMap contributors",
      }).addTo(map);

      let records = [];
      let markers = [];
      let speciesChart, municipalityChart, dashboardChart = null;

      // =====================================================
      // ESTADOS GLOBAIS MULTI-SELECT
      // =====================================================
      const AVAILABLE_COLORMAPS = ["Greens5", "YlOrRd", "Blues", "RdPu"];
      let activeSpeciesConfigs = {}; // { "Especie": { period, scenario, binary, colormap, layer... } }
      let activeMunicipalities = []; // Array de nomes de municípios selecionados
      let currentZIndex = 400; // Para colocar a última espécie alterada por cima

      // GeoJSON dos municípios
      const MUNICIPIOS_GEOJSON_URL = "https://raw.githubusercontent.com/nmota/caop_GeoJSON/master/Portugal_Municipalities.geojson";
      let municipiosGeoJSON = null;
      let municipioHighlightLayer = null;

// =====================================================
// FUNÇÕES DE UI E FILTROS (Copiar isto para o script.js)
// =====================================================

window.toggleDropdown = function(id) {
    const el = document.getElementById(id);
    if (el) el.hidden = !el.hidden;
};

window.filterOptions = function(input, containerId) {
    const filter = input.value.toUpperCase();
    const container = document.getElementById(containerId);
    container.querySelectorAll('.options-list label').forEach(l => {
        l.style.display = l.textContent.toUpperCase().includes(filter) ? "" : "none";
    });
};

window.setAll = function(containerId, state) {
    const container = document.getElementById(containerId);
    container.querySelectorAll('.options-list label').forEach(label => {
        if (label.style.display !== 'none') {
            const cb = label.querySelector('input[type="checkbox"]');
            if (cb) {
                cb.checked = state;
                cb.dispatchEvent(new Event('change'));
            }
        }
    });
};






// Filtra a lista visível no dropdown enquanto escreves
function filterOptions(input, containerId) {
    const filter = input.value.toUpperCase();
    const container = document.getElementById(containerId);
    // Procura todas as labels dentro da lista (assumindo que o HTML tem .options-list)
    const labels = container.querySelectorAll('.options-list label');
    
    labels.forEach(label => {
        const text = label.textContent || label.innerText;
        // Mostra a label se o texto corresponder ao que escreveste
        label.style.display = text.toUpperCase().includes(filter) ? "" : "none";
    });
}

// Seleciona ou Limpa todas as checkboxes visíveis (respeitando o filtro de pesquisa)
function setAll(containerId, state) {
    const container = document.getElementById(containerId);
    // Seleciona apenas os inputs que estão visíveis
    container.querySelectorAll('.options-list input[type="checkbox"]').forEach(cb => {
        if (cb.parentElement.style.display !== 'none') {
            cb.checked = state;
            // Dispara o evento 'change' para o mapa reagir
            cb.dispatchEvent(new Event('change'));
        }
    });
}




      async function loadMunicipiosGeoJSON() {
        if (municipiosGeoJSON) return municipiosGeoJSON;
        try {
          const resp = await fetch(MUNICIPIOS_GEOJSON_URL);
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          municipiosGeoJSON = await resp.json();
        } catch (e) {
          console.error("[municipios] Erro:", e);
        }
        return municipiosGeoJSON;
      }




// Exemplo para Espécies (faz o mesmo para Municípios)
async function loadSpecies() {
  const response = await fetch(`${API_URL}/species`);
  const speciesList = await response.json();
  const container = document.querySelector("#speciesOptions .options-list");
  container.innerHTML = '';
  speciesList.forEach((species) => {
    const label = document.createElement("label");
    label.className = "multiselect-option";
    label.innerHTML = `<input type="checkbox" value="${species}" onchange="handleSpeciesCheckbox(this)"> ${species.replace(/_/g, " ")}`;
    container.appendChild(label);
  });
}
      // =====================================================
      // UI: DROPDOWNS E CHECKBOXES
      // =====================================================

      // Fecha menus se clicarmos fora
      document.addEventListener("click", (evt) => {
        document.querySelectorAll(".custom-multiselect").forEach(dropdown => {
          if (!dropdown.contains(evt.target)) {
            const optionsDiv = dropdown.querySelector(".multiselect-options");
            if (optionsDiv) optionsDiv.hidden = true;
          }
        });
      });

      // =====================================================
      // LÓGICA DE MUNICÍPIOS (HIGHLIGHTS E FILTRO)
      // =====================================================
      function handleMuniCheckbox(checkbox) {
        if (checkbox.checked) {
          activeMunicipalities.push(checkbox.value);
        } else {
          activeMunicipalities = activeMunicipalities.filter(m => m !== checkbox.value);
        }
        
        document.getElementById("muniSelectLabel").textContent = activeMunicipalities.length === 0 
          ? "Todos os municípios" 
          : `${activeMunicipalities.length} município(s) focado(s)`;
          
        filterPoints();
        highlightMunicipalities();
      }

      async function highlightMunicipalities() {
        if (municipioHighlightLayer) {
          map.removeLayer(municipioHighlightLayer);
          municipioHighlightLayer = null;
        }
        
        if (activeMunicipalities.length === 0) return;

        try {
          const geojson = await loadMunicipiosGeoJSON();
          if (!geojson) return;

          const norm = s => s.toUpperCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
          const procurados = activeMunicipalities.map(m => norm(m));

          // Encontra TODOS os features correspondentes
          const features = geojson.features.filter(f =>
            procurados.includes(norm(f.properties.Concelho || ""))
          );

          if (features.length === 0) return;

          municipioHighlightLayer = L.geoJSON(features, {
            style: {
              color: "#cc0000",
              weight: 3,
              fillColor: "#ff4444",
              fillOpacity: 0.15,
              dashArray: "6 3",
            }
          }).addTo(map);

          map.fitBounds(municipioHighlightLayer.getBounds(), { padding: [40, 40] });
        } catch (e) {
          console.error("Erro ao highlight municípios:", e);
        }
      }

      // =====================================================
      // LÓGICA DE ESPÉCIES (CARTÕES E RASTERS)
      // =====================================================
      function handleSpeciesCheckbox(checkbox) {
        const species = checkbox.value;
        if (checkbox.checked) {
          addSpeciesLayer(species);
        } else {
          removeSpeciesLayer(species);
        }
      }

      function toggleCardBody(safeSp) {
        const body = document.getElementById(`body-${safeSp}`);
        body.style.display = body.style.display === "none" ? "block" : "none";
      }

      window.removeSpeciesFromCard = function(species, event) {
        event.stopPropagation();
        const checkbox = document.querySelector(`#speciesOptions input[value="${species}"]`);
        if (checkbox) checkbox.checked = false;
        removeSpeciesLayer(species);
      };

      function addSpeciesLayer(species) {
        if (activeSpeciesConfigs[species]) return;

        const colorIndex = Object.keys(activeSpeciesConfigs).length % AVAILABLE_COLORMAPS.length;
        const assignedColormap = AVAILABLE_COLORMAPS[colorIndex];

        activeSpeciesConfigs[species] = {
          period: "hist",
          scenario: "ssp370",
          binary: true,
          colormap: assignedColormap,
          layer: null,
          abortController: null,
          hasZoomed: false
        };

        const safeSp = species.replace(/\s+/g, '_');
        const colorStops = COLORMAP_STOPS[assignedColormap];
        const dotColor = colorStops[colorStops.length - 1];

        const cardHtml = `
          <div class="species-card" id="card-${safeSp}">
            <div class="species-card-header" onclick="toggleCardBody('${safeSp}')">
              <span style="display:flex; align-items:center; gap:8px;">
                <span style="display:inline-block; width:12px; height:12px; border-radius:50%; background:${dotColor};"></span>
                ${species.replace(/_/g, " ")}
              </span>
              <button class="remove-btn" onclick="removeSpeciesFromCard('${species}', event)">×</button>
            </div>
            <div class="species-card-body" id="body-${safeSp}">
              
              <div style="display:grid; grid-template-columns: 1fr 1fr; gap:8px;">
                <div>
                  <label>Tipo</label>
                  <select onchange="updateSpeciesConfig('${species}', 'binary', this.value === 'true')">
                    <option value="true" selected>Binário</option>
                    <option value="false">Contínuo</option>
                  </select>
                </div>
                <div>
                  <label>Paleta</label>
                  <select onchange="updateSpeciesConfig('${species}', 'colormap', this.value)">
                    <option value="Greens5" ${assignedColormap === "Greens5" ? "selected" : ""}>Greens</option>
                    <option value="YlOrRd" ${assignedColormap === "YlOrRd" ? "selected" : ""}>YlOrRd</option>
                    <option value="Blues" ${assignedColormap === "Blues" ? "selected" : ""}>Blues</option>
                    <option value="RdPu" ${assignedColormap === "RdPu" ? "selected" : ""}>RdPu</option>
                  </select>
                </div>
              </div>

              <label>Período</label>
              <select onchange="updateSpeciesConfig('${species}', 'period', this.value)">
                <option value="hist" selected>Histórico (1981-2024)</option>
                <option value="2041-2070">Futuro Médio (2041-2070)</option>
                <option value="2071-2100">Futuro Longo (2071-2100)</option>
              </select>

              <div id="scen-container-${safeSp}" hidden>
                <label>Cenário (SSP)</label>
                <select onchange="updateSpeciesConfig('${species}', 'scenario', this.value)">
                  <option value="ssp126">SSP1-2.6 (Baixas)</option>
                  <option value="ssp370" selected>SSP3-7.0 (Intermédias)</option>
                  <option value="ssp585">SSP5-8.5 (Altas)</option>
                </select>
              </div>

              <div class="raster-info" id="info-${safeSp}">A descarregar dados...</div>
            </div>
          </div>
        `;

        document.getElementById("rasterCardsContainer").insertAdjacentHTML('afterbegin', cardHtml);
        
        document.getElementById("speciesSelectLabel").textContent = `${Object.keys(activeSpeciesConfigs).length} espécie(s) no mapa`;
        filterPoints();
        fetchAndDrawRaster(species);
      }

      function removeSpeciesLayer(species) {
        if (!activeSpeciesConfigs[species]) return;
        if (activeSpeciesConfigs[species].abortController) activeSpeciesConfigs[species].abortController.abort();
        if (activeSpeciesConfigs[species].layer) map.removeLayer(activeSpeciesConfigs[species].layer);
        
        delete activeSpeciesConfigs[species];

        const safeSp = species.replace(/\s+/g, '_');
        const card = document.getElementById(`card-${safeSp}`);
        if (card) card.remove();

        const count = Object.keys(activeSpeciesConfigs).length;
        document.getElementById("speciesSelectLabel").textContent = count === 0 ? "Selecionar Espécies..." : `${count} espécie(s) no mapa`;
        
        rasterLegend.update();
        filterPoints();
      }

      window.updateSpeciesConfig = function(species, key, value) {
        if (!activeSpeciesConfigs[species]) return;
        activeSpeciesConfigs[species][key] = value;
        const safeSp = species.replace(/\s+/g, '_');

        if (key === 'period') {
          document.getElementById(`scen-container-${safeSp}`).hidden = (value === 'hist');
        }

        if (key === 'colormap') {
           const colorStops = COLORMAP_STOPS[value];
           const dotColor = colorStops[colorStops.length - 1];
           document.querySelector(`#card-${safeSp} .species-card-header span span`).style.background = dotColor;
        }

        fetchAndDrawRaster(species);
      };

      async function fetchAndDrawRaster(species) {
        const config = activeSpeciesConfigs[species];
        const safeSp = species.replace(/\s+/g, '_');
        const infoDiv = document.getElementById(`info-${safeSp}`);
        
        if (config.abortController) config.abortController.abort();
        config.abortController = new AbortController();
        const signal = config.abortController.signal;

        try {
          infoDiv.textContent = "A atualizar camada...";
          const encodedSpecies = encodeURIComponent(species);

          let url = `${API_URL}/raster/overlay-info/${encodedSpecies}?period=${config.period}&colormap=${config.colormap}&binary=${config.binary}`;
          if (config.period !== "hist") url += `&scenario=${config.scenario}`;

          const overlayInfoResp = await fetch(url, { signal });
          if (!overlayInfoResp.ok) throw new Error(`Sem modelo para esta seleção.`);
          const overlayInfo = await overlayInfoResp.json();

          const cacheBuster = `cb=${Date.now()}`;
          const tileUrl = `${API_URL}${overlayInfo.tile_url}&${cacheBuster}`;

          if (config.layer) map.removeLayer(config.layer);

          currentZIndex++;
          config.layer = L.imageOverlay(tileUrl, overlayInfo.bounds, {
            opacity: 0.7,
            zIndex: currentZIndex
          }).addTo(map);

          if (Object.keys(activeSpeciesConfigs).length === 1 && !config.hasZoomed && activeMunicipalities.length === 0) {
             map.fitBounds(overlayInfo.bounds);
             config.hasZoomed = true;
          }

          rasterLegend.update();

          // Tentar buscar estatísticas adicionais
          let statsUrl = `${API_URL}/raster/stats/${encodedSpecies}?period=${config.period}&binary=${config.binary}`;
          if (config.period !== "hist") statsUrl += `&scenario=${config.scenario}`;
          
          let areaUrl = `${API_URL}/raster/suitable-area/${encodedSpecies}?period=${config.period}`;
          if (config.period !== "hist") areaUrl += `&scenario=${config.scenario}`;

          const [statsResponse, areaResponse, summaryResponse] = await Promise.all([
            fetch(statsUrl, { signal }).catch(() => null),
            fetch(areaUrl, { signal }).catch(() => null),
            fetch(`${API_URL}/raster/summary/${encodedSpecies}`, { signal }).catch(() => null)
          ]);

          let areaInfo = '';
          if (areaResponse && areaResponse.ok) {
            const areaData = await areaResponse.json();
            if (areaData.data && areaData.data.suitable_area_km2 !== undefined) {
              areaInfo = `${areaData.data.suitable_area_km2.toLocaleString()} km² adequados`;
            }
          }

          let extraInfo = '';
          if (summaryResponse && summaryResponse.ok) {
            const summaryData = await summaryResponse.json();
            const trend = summaryData.scenario_analysis?.summary?.trend;
            if (trend) {
              const trendIcon = trend === 'expansão' ? '↗️' : (trend === 'contração' ? '↘️' : '➡️');
              extraInfo = `<br>Tendência: ${trendIcon} <strong>${trend}</strong>`;
            }
          }

          if (statsResponse && statsResponse.ok) {
            const stats = await statsResponse.json();
            infoDiv.innerHTML = `Píxeis ativos: <strong>${stats.stats.count.toLocaleString()}</strong><br>${areaInfo}${extraInfo}`;
          } else {
            infoDiv.innerHTML = `Camada desenhada. Estatísticas indisponíveis.`;
          }

        } catch (error) {
          if (error.name === "AbortError") return;
          if (config.layer) { map.removeLayer(config.layer); config.layer = null; }
          rasterLegend.update();
          infoDiv.innerHTML = `<span style="color:#cc0000;">⚠️ ${error.message}</span>`;
        }
      }

      // =====================================================
      // LEGENDA DO RASTER (MULTI-ESPÉCIES)
      // =====================================================
      const COLORMAP_STOPS = {
        "Greens5": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
        "YlOrRd":  ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"],
        "Greens":  ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
        "Blues":   ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
        "RdPu":    ["#feebe2", "#fbb4b9", "#f768a1", "#ae017e", "#49006a"],
      };

      const rasterLegend = L.control({ position: "bottomright" });
      rasterLegend.onAdd = function () {
        this._div = L.DomUtil.create("div", "raster-legend");
        this._div.style.display = "none";
        return this._div;
      };
      
      rasterLegend.update = function () {
        const activeKeys = Object.keys(activeSpeciesConfigs);
        if (activeKeys.length === 0) {
          this._div.style.display = "none";
          return;
        }

        let html = `<h4>Legenda (SDM)</h4>`;
        
        activeKeys.forEach(species => {
          const config = activeSpeciesConfigs[species];
          const stops = COLORMAP_STOPS[config.colormap] || COLORMAP_STOPS["Greens5"];
          const darkestColor = stops[stops.length - 1];

          html += `<div class="legend-item"><span class="legend-sp-name">${species.replace(/_/g, " ")}</span>`;
          
          if (config.binary) {
            html += `
              <div class="legend-binary">
                <div class="legend-swatch" style="background:${darkestColor}; opacity:0.8;"></div>
                <span>Adequado</span>
              </div>
              <div class="legend-binary">
                <div class="legend-swatch legend-swatch-absent"></div>
                <span>Não adequado</span>
              </div>`;
          } else {
            const gradient = `linear-gradient(to right, ${stops.join(", ")})`;
            html += `
              <div class="legend-gradient" style="background:${gradient};"></div>
              <div class="legend-labels"><span>Não adequado</span><span>Adequado</span></div>`;
          }
          html += `</div>`;
        });

        this._div.innerHTML = html;
        this._div.style.display = "block";
      };
      rasterLegend.addTo(map);

      // =====================================================
      // CARREGAMENTO DE DADOS INICIAIS
      // =====================================================
      async function loadRecords() {
        const response = await fetch(`${API_URL}/records`);
        records = await response.json();
        filterPoints();
      }

      const TODOS_MUNICIPIOS = [
        "Abrantes","Águeda","Aguiar da Beira","Alandroal","Albergaria-a-Velha","Albufeira","Alcácer do Sal","Alcanena","Alcobaça","Alcochete","Alcoutim","Alenquer","Alfândega da Fé","Alijó","Aljezur","Aljustrel","Almada","Almeida","Almeirim","Almodôvar","Alpiarça","Alter do Chão","Alvaiázere","Alvito","Amadora","Amarante","Amares","Anadia","Angra do Heroísmo","Ansião","Arcos de Valdevez","Arganil","Armamar","Arouca","Arraiolos","Arronches","Arruda dos Vinhos","Aveiro","Avis","Azambuja",
        "Baião","Barcelos","Barrancos","Barreiro","Batalha","Beja","Belmonte","Benavente","Bombarral","Borba","Boticas","Braga","Bragança","Cabeceiras de Basto","Cadaval","Caldas da Rainha","Câmara de Lobos","Caminha","Cantanhede","Carregal do Sal","Cartaxo","Cascais","Castanheira de Pêra","Castelo Branco","Castelo de Paiva","Castelo de Vide","Castro Daire","Castro Marim","Castro Verde","Celorico da Beira","Celorico de Basto","Chamusca","Chaves","Cinfães","Coimbra","Condeixa-a-Nova","Constância","Coruche","Covilhã","Crato","Cuba",
        "Elvas","Entroncamento","Espinho","Esposende","Estarreja","Estremoz","Évora","Fafe","Faro","Felgueiras","Ferreira do Alentejo","Ferreira do Zêzere","Figueira da Foz","Figueira de Castelo Rodrigo","Figueiró dos Vinhos","Fornos de Algodres","Freixo de Espada à Cinta","Fronteira","Funchal","Fundão",
        "Gavião","Góis","Golegã","Gondomar","Gouveia","Grândola","Guarda","Guimarães","Horta","Idanha-a-Nova","Ílhavo","Lagoa","Lagos","Lamego","Leiria","Lisboa","Loulé","Loures","Lousã","Lousada","Mação","Macedo de Cavaleiros","Machico","Mafra","Maia","Mangualde","Manteigas","Marco de Canaveses","Marinha Grande","Marvão","Matosinhos","Mealhada","Mêda","Melgaço","Mesão Frio","Miranda do Corvo","Miranda do Douro","Mirandela","Mogadouro","Moimenta da Beira","Moita","Monção","Monchique","Mondim de Basto","Monforte","Montalegre","Montemor-o-Novo","Montemor-o-Velho","Montijo","Mora","Mortágua","Moura","Mourão","Murça","Murtosa",
        "Nazaré","Nelas","Nisa","Nordeste","Óbidos","Odemira","Odivelas","Oeiras","Olhão","Oliveira de Azeméis","Oliveira de Frades","Oliveira do Bairro","Oliveira do Hospital","Ourém","Ourique","Ovar","Paços de Ferreira","Palmela","Pampilhosa da Serra","Paredes","Paredes de Coura","Pedrogão Grande","Penacova","Penafiel","Penalva do Castelo","Penamacor","Penedono","Penela","Peniche","Peso da Régua","Pinhel","Pombal","Ponte da Barca","Ponte de Lima","Ponte de Sor","Portalegre","Portel","Portimão","Porto","Porto de Mós","Porto Moniz","Porto Santo","Póvoa de Lanhoso","Póvoa de Varzim","Praia da Vitória",
        "Proença-a-Nova","Resende","Ribeira Brava","Ribeira de Pena","Ribeira Grande","Rio Maior","Sabrosa","Sabugal","Salvaterra de Magos","Santa Comba Dão","Santa Cruz","Santa Cruz da Graciosa","Santa Cruz das Flores","Santa Maria da Feira","Santa Marta de Penaguião","Santarém","Santiago do Cacém","Santo Tirso","São Brás de Alportel","São João da Madeira","São João da Pesqueira","São Pedro do Sul","São Roque do Pico","São Vicente","Sardoal","Sátão","Seia","Seixal","Sernancelhe","Serpa","Sertã","Sesimbra","Setúbal","Sever do Vouga","Silves","Sines","Sintra","Sobral de Monte Agraço","Soure","Sousel",
        "Tábua","Tabuaço","Tarouca","Tavira","Terras de Bouro","Tomar","Tondela","Torre de Moncorvo","Torres Novas","Torres Vedras","Trancoso","Trofa","Vagos","Vale de Cambra","Valença","Valongo","Valpaços","Vendas Novas","Viana do Alentejo","Viana do Castelo","Vidigueira","Vieira do Minho","Vila de Rei","Vila do Bispo","Vila do Conde","Vila do Porto","Vila Flor","Vila Franca de Xira","Vila Franca do Campo","Vila Nova da Barquinha","Vila Nova de Cerveira","Vila Nova de Famalicão","Vila Nova de Foz Côa","Vila Nova de Gaia","Vila Nova de Paiva","Vila Nova de Poiares","Vila Pouca de Aguiar","Vila Real","Vila Real de Santo António","Vila Velha de Ródão","Vila Verde","Vila Viçosa","Vimioso","Vinhais","Viseu","Vizela","Vouzela"
      ].sort((a, b) => a.localeCompare(b, "pt"));

async function loadMunicipalities() {
  const optionsContainer = document.querySelector("#muniOptions .options-list"); 
  TODOS_MUNICIPIOS.forEach((nome) => {
    const label = document.createElement("label");
    label.className = "multiselect-option";
    label.innerHTML = `<input type="checkbox" value="${nome}" onchange="handleMuniCheckbox(this)"> ${nome}`;
    optionsContainer.appendChild(label);
  });
}

      function filterPoints() {
        const activeSpeciesList = Object.keys(activeSpeciesConfigs);
        
        const filteredRecords = records.filter((record) => {
          const speciesMatch = activeSpeciesList.length === 0 || activeSpeciesList.includes(record.species.replace(/_/g, " "));
          const municipalityMatch = activeMunicipalities.length === 0 || activeMunicipalities.includes(record.municipality);
          return speciesMatch && municipalityMatch;
        });
        
        markers.forEach((marker) => marker.remove());
        markers = [];
        filteredRecords.forEach((record) => {
          const marker = L.marker([record.latitude, record.longitude]).addTo(map)
            .bindPopup(`<strong>${record.species}</strong><br>Município: ${record.municipality}<br>Data: ${record.date}<br>Fonte: ${record.source}`);
          markers.push(marker);
        });
      }

      // =====================================================
      // INDICADORES, GRÁFICOS E RELATÓRIOS
      // =====================================================
async function loadSummary() {
  document.getElementById("summary").innerHTML = `<div class="metric">A carregar indicadores...</div>`;
  
  try {
    const resp = await fetch(`${API_URL}/dashboard-stats`);
    const data = await resp.json();

    // Top 5 espécies
    const top5SpHtml = data.top5_species.map((s, i) =>
      `<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #eee;font-size:12px;">
        <span>${i+1}. <em>${s.species}</em></span>
        <span style="color:#2d6a4f;font-weight:bold;">${s.pixel_count.toLocaleString()} px</span>
      </div>`
    ).join("");

    // Top 5 municípios
    const top5MunHtml = data.top5_municipalities.map((m, i) =>
      `<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #eee;font-size:12px;">
        <span>${i+1}. ${m.municipality}</span>
        <span style="color:#1b4332;font-weight:bold;">${m.records} reg.</span>
      </div>`
    ).join("");

    document.getElementById("summary").innerHTML = `
      <div class="metric">
        <strong>Total de espécies (rasters):</strong><br>
        <span style="font-size:20px;font-weight:bold;color:#2d6a4f;">${data.total_raster_species}</span>
      </div>
      <div class="metric">
        <strong>Total de registos de campo:</strong><br>
        <span style="font-size:20px;font-weight:bold;color:#1b4332;">${data.total_records}</span>
      </div>
      <div class="metric">
        <strong>Top 5 espécies (área SDM)</strong>
        ${top5SpHtml}
      </div>
      <div class="metric">
        <strong>Top 5 municípios (registos)</strong>
        ${top5MunHtml}
      </div>
      <div class="metric">
        <strong>Distribuição top 5 espécies</strong>
        <canvas id="dashboardChart" style="max-height:200px;margin-top:8px;"></canvas>
      </div>`;

    // Gráfico pizza
    if (dashboardChart) dashboardChart.destroy();
    dashboardChart = new Chart(document.getElementById("dashboardChart"), {
      type: "doughnut",
      data: {
        labels: data.top5_species.map(s => s.species),
        datasets: [{
          data: data.top5_species.map(s => s.pixel_count),
          backgroundColor: ["#2d6a4f","#52b788","#95d5b2","#1b4332","#74c69d"],
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        plugins: { legend: { position: "bottom", labels: { font: { size: 10 } } } }
      }
    });

  } catch (e) {
    document.getElementById("summary").innerHTML = `<div class="metric" style="color:red;">Erro ao carregar indicadores.</div>`;
    console.error(e);
  }
}

     async function generateTemplateReport() {
  document.getElementById("report").textContent = "A gerar relatório por template...";
  document.getElementById("pdfButton").disabled = true;
  const level = document.getElementById("reportLevel").value;
  const activeSpeciesList = Object.keys(activeSpeciesConfigs);
  const species = activeSpeciesList.length === 1 ? activeSpeciesList[0] : null;
  const municipality = activeMunicipalities.length === 1 ? activeMunicipalities[0] : null;
  const params = new URLSearchParams({ level });
  if (species) params.append("species", species);
  if (municipality) params.append("municipality", municipality);
  const resp = await fetch(`${API_URL}/report-template?${params}`, { method: "POST" });
  const data = await resp.json();
  document.getElementById("report").textContent = data.report;
  document.getElementById("pdfButton").disabled = false;
}

async function generateAIReport() {
  document.getElementById("report").textContent = "A gerar relatório com IA (Ollama)...";
  document.getElementById("pdfButton").disabled = true;
  const level = document.getElementById("reportLevel").value;
  const municipality = activeMunicipalities.length > 0 ? activeMunicipalities[0] : null;

  // Construir lista com os filtros ativos de cada espécie
  const speciesConfigs = Object.entries(activeSpeciesConfigs).map(([sp, cfg]) => ({
    species: sp,
    period: cfg.period,
    scenario: cfg.scenario,
    binary: cfg.binary,
  }));

  const params = new URLSearchParams({ level });
  if (municipality) params.append("municipality", municipality);
  
  try {
    console.log("species_configs enviados:", JSON.stringify({ species_configs: speciesConfigs }, null, 2));
    const resp = await fetch(`${API_URL}/report?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ species_configs: speciesConfigs }),
    });
    const data = await resp.json();
    const validationText = data.validation?.problems?.length
      ? `\n\nValidação: ${data.validation.problems.join("; ")}`
      : "";
    document.getElementById("report").textContent =
      `[Fonte: ${data.source} | Nível: ${data.level}]\n\n${data.report}${validationText}`;
  } catch (e) {
    document.getElementById("report").textContent = "Erro ao contactar o Ollama. Verifica se está a correr.";
  }
  document.getElementById("pdfButton").disabled = false;
}
async function exportPdf() {
  const level = document.getElementById("reportLevel").value;
  const municipality = activeMunicipalities.length > 0 ? activeMunicipalities[0] : null;

  const speciesConfigs = Object.entries(activeSpeciesConfigs).map(([sp, cfg]) => ({
    species: sp,
    period: cfg.period,
    scenario: cfg.scenario,
    binary: cfg.binary ?? true,
    colormap: cfg.colormap ?? "Greens5",
  }));

  const params = new URLSearchParams({ level });
  if (municipality) params.append("municipality", municipality);

  try {
    const resp = await fetch(`${API_URL}/report/pdf?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ species_configs: speciesConfigs }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `relatorio_${level}.pdf`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("Erro ao exportar PDF: " + e.message);
  }
}

document.getElementById("templateButton").addEventListener("click", generateTemplateReport);
document.getElementById("reportButton").addEventListener("click", generateAIReport);
document.getElementById("pdfButton").addEventListener("click", exportPdf);

      // Inicia a aplicação
// Inicia a aplicação
loadRecords();
loadSpecies();
loadMunicipalities();
loadSummary();