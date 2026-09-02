export type ProfileLocationRegion = {
  name: string;
  timezone: string;
  cities: string[];
};

type ProfileLocationCountry = {
  timezone: string;
  regions: ProfileLocationRegion[];
};

const locationCatalogue: Record<string, ProfileLocationCountry> = {
  GB: {
    timezone: "Europe/London",
    regions: [
      { name: "England", timezone: "Europe/London", cities: ["London", "Birmingham", "Manchester", "Liverpool", "Leeds", "Bristol", "Newcastle upon Tyne", "Nottingham", "Sheffield", "Brighton", "Oxford", "Cambridge"] },
      { name: "Scotland", timezone: "Europe/London", cities: ["Edinburgh", "Glasgow", "Aberdeen", "Dundee", "Inverness", "Stirling", "Perth"] },
      { name: "Wales", timezone: "Europe/London", cities: ["Cardiff", "Swansea", "Newport", "Wrexham", "Bangor", "St Davids"] },
      { name: "Northern Ireland", timezone: "Europe/London", cities: ["Belfast", "Derry", "Lisburn", "Newry", "Armagh"] },
    ],
  },
  PT: {
    timezone: "Europe/Lisbon",
    regions: [
      { name: "Aveiro", timezone: "Europe/Lisbon", cities: ["Aveiro", "Águeda", "Espinho", "Ovar"] },
      { name: "Braga", timezone: "Europe/Lisbon", cities: ["Braga", "Barcelos", "Guimarães", "Vila Nova de Famalicão"] },
      { name: "Coimbra", timezone: "Europe/Lisbon", cities: ["Coimbra", "Figueira da Foz", "Cantanhede"] },
      { name: "Faro", timezone: "Europe/Lisbon", cities: ["Faro", "Albufeira", "Lagos", "Portimão", "Tavira"] },
      { name: "Leiria", timezone: "Europe/Lisbon", cities: ["Leiria", "Alcobaça", "Caldas da Rainha", "Nazaré"] },
      { name: "Lisbon", timezone: "Europe/Lisbon", cities: ["Lisbon", "Cascais", "Sintra", "Amadora", "Oeiras", "Mafra"] },
      { name: "Porto", timezone: "Europe/Lisbon", cities: ["Porto", "Gaia", "Matosinhos", "Maia", "Póvoa de Varzim"] },
      { name: "Setúbal", timezone: "Europe/Lisbon", cities: ["Setúbal", "Almada", "Barreiro", "Sesimbra"] },
      { name: "Madeira", timezone: "Atlantic/Madeira", cities: ["Funchal", "Machico", "Santa Cruz", "Câmara de Lobos"] },
      { name: "Azores", timezone: "Atlantic/Azores", cities: ["Ponta Delgada", "Angra do Heroísmo", "Horta", "Ribeira Grande"] },
    ],
  },
  US: {
    timezone: "America/New_York",
    regions: [
      { name: "California", timezone: "America/Los_Angeles", cities: ["Los Angeles", "San Diego", "San Francisco", "San Jose", "Sacramento"] },
      { name: "Florida", timezone: "America/New_York", cities: ["Miami", "Orlando", "Tampa", "Jacksonville", "Tallahassee"] },
      { name: "Illinois", timezone: "America/Chicago", cities: ["Chicago", "Aurora", "Rockford", "Springfield"] },
      { name: "Massachusetts", timezone: "America/New_York", cities: ["Boston", "Worcester", "Cambridge", "Springfield"] },
      { name: "Nevada", timezone: "America/Los_Angeles", cities: ["Las Vegas", "Henderson", "Reno", "Carson City"] },
      { name: "New York", timezone: "America/New_York", cities: ["New York", "Buffalo", "Rochester", "Albany"] },
      { name: "Texas", timezone: "America/Chicago", cities: ["Houston", "San Antonio", "Dallas", "Austin", "Fort Worth"] },
      { name: "Washington", timezone: "America/Los_Angeles", cities: ["Seattle", "Spokane", "Tacoma", "Olympia"] },
      { name: "District of Columbia", timezone: "America/New_York", cities: ["Washington"] },
    ],
  },
};

export function defaultTimezoneForCountry(countryCode: string, browserTimezone = ""): string {
  return locationCatalogue[countryCode]?.timezone || browserTimezone;
}

export function regionsForCountry(countryCode: string): ProfileLocationRegion[] {
  return locationCatalogue[countryCode]?.regions ?? [];
}

export function regionForName(countryCode: string, regionName: string): ProfileLocationRegion | undefined {
  return regionsForCountry(countryCode).find((region) => region.name === regionName);
}
