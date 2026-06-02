# TODO

- [ ] Inspect current raster switching logic in `frontend/index.html` (already located in `showRasterLayer`).
- [ ] Update frontend so switching species updates the raster immediately without zoom-triggered repaint issues:
  - [ ] Remove/limit `map.fitBounds(...)` on every species change.
  - [ ] Ensure the new overlay is attached only after the image is loaded (preload).
  - [ ] Add a cache-busting query param to the tile URL to prevent browser from showing the previous cached image.
- [ ] Make the above edits in `invasive-species-ai-app/frontend/index.html`.
- [ ] Run the app / sanity-check manually (switch species at same zoom; confirm no raster change on zoom).

