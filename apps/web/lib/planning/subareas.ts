// Copyright (c) 2026 Qnit. All rights reserved.
// SPDX-License-Identifier: LicenseRef-Proprietary
//
// Sub-areas of each 2031 plan: the named places checked on the map in the US-02 layer test
// (2 Oct 2026, "2031 zone layers" screenshots), with the point and zoom each was viewed at.
// Picking one in the toolbar flies the map there and switches the plan's zones on.

export interface SubArea {
  name: string;
  lat: number;
  lng: number;
  zoom: number;
}

export const PLAN_SUBAREAS: Record<string, SubArea[]> = {
  "BDA-RMP2031": [
    { name: "Banashankari", lat: 12.9255, lng: 77.5468, zoom: 16 },
    { name: "Begur", lat: 12.878, lng: 77.625, zoom: 16 },
    { name: "Bellandur lake (NGT buffer)", lat: 12.935, lng: 77.665, zoom: 16 },
    { name: "Bommanahalli", lat: 12.9, lng: 77.63, zoom: 16 },
    { name: "BTM Layout", lat: 12.9166, lng: 77.6101, zoom: 16 },
    { name: "CBD / MG Road", lat: 12.975, lng: 77.606, zoom: 16 },
    { name: "Electronic City", lat: 12.845, lng: 77.66, zoom: 16 },
    { name: "Hebbal", lat: 13.0358, lng: 77.597, zoom: 16 },
    { name: "Hennur", lat: 13.04, lng: 77.64, zoom: 16 },
    { name: "Hesaraghatta (forest)", lat: 13.135, lng: 77.5, zoom: 16 },
    { name: "HSR Layout", lat: 12.9116, lng: 77.6389, zoom: 16 },
    { name: "Indiranagar", lat: 12.9784, lng: 77.6408, zoom: 16 },
    { name: "Jakkur", lat: 13.07, lng: 77.61, zoom: 16 },
    { name: "Jayanagar", lat: 12.925, lng: 77.583, zoom: 16 },
    { name: "JP Nagar", lat: 12.9063, lng: 77.5857, zoom: 16 },
    { name: "Kanakapura Road", lat: 12.87, lng: 77.56, zoom: 16 },
    { name: "Kengeri", lat: 12.91, lng: 77.485, zoom: 16 },
    { name: "Koramangala", lat: 12.9352, lng: 77.6245, zoom: 16 },
    { name: "KR Puram", lat: 13.008, lng: 77.695, zoom: 16 },
    { name: "Mahadevapura", lat: 12.99, lng: 77.7, zoom: 16 },
    { name: "Malleshwaram", lat: 13.0035, lng: 77.57, zoom: 16 },
    { name: "Marathahalli", lat: 12.956, lng: 77.701, zoom: 16 },
    { name: "Peenya industrial", lat: 13.03, lng: 77.52, zoom: 16 },
    { name: "Rajajinagar", lat: 12.991, lng: 77.555, zoom: 16 },
    { name: "RR Nagar", lat: 12.926, lng: 77.518, zoom: 16 },
    { name: "Varthur lake", lat: 12.945, lng: 77.74, zoom: 16 },
    { name: "Whitefield", lat: 12.97, lng: 77.75, zoom: 16 },
    { name: "Yelahanka", lat: 13.1, lng: 77.595, zoom: 16 },
    { name: "Yeshwanthpur", lat: 13.025, lng: 77.54, zoom: 16 },
  ],
  "BMRDA-HSK-MP2031": [
    { name: "Hoskote town", lat: 13.07, lng: 77.798, zoom: 16 },
    { name: "Hoskote north", lat: 13.085, lng: 77.8, zoom: 16 },
    { name: "Hoskote south", lat: 13.055, lng: 77.79, zoom: 16 },
    { name: "Jadigenahalli", lat: 13.0764, lng: 77.896, zoom: 16 },
    { name: "Kattigenahalli", lat: 13.1, lng: 77.83, zoom: 16 },
    { name: "Pillagumpe", lat: 13.04, lng: 77.78, zoom: 16 },
  ],
  "BMRDA-ANK-MP2031": [
    { name: "Anekal town", lat: 12.71, lng: 77.695, zoom: 16 },
    { name: "Anekal east", lat: 12.71, lng: 77.71, zoom: 16 },
    { name: "Attibele", lat: 12.78, lng: 77.765, zoom: 16 },
    { name: "Attibele town", lat: 12.779, lng: 77.772, zoom: 16 },
    { name: "Bommasandra industrial", lat: 12.815, lng: 77.69, zoom: 16 },
    { name: "Chandapura", lat: 12.8, lng: 77.7, zoom: 16 },
    { name: "Dommasandra", lat: 12.88, lng: 77.76, zoom: 16 },
    { name: "Hennagara", lat: 12.77, lng: 77.65, zoom: 16 },
    { name: "Jigani industrial", lat: 12.785, lng: 77.64, zoom: 16 },
    { name: "Sarjapura", lat: 12.86, lng: 77.785, zoom: 16 },
    { name: "Sarjapura town", lat: 12.86, lng: 77.788, zoom: 16 },
  ],
  "BMRDA-NLM-MP2031": [
    { name: "Nelamangala town", lat: 13.0976, lng: 77.4041, zoom: 15 },
  ],
};
