export type NavLink = {
  label: string;
  href: string;
  external?: boolean;
};

export type NavGroup = {
  label: string;
  links: NavLink[];
};

export function navigation(latestPlanHref: string): NavGroup[] {
  return [
    {
      label: "Overview",
      links: [
        { label: "Home", href: "/" },
        { label: "AI Greenhouse", href: "/start/ai-greenhouse" },
        { label: "Latest Plan", href: latestPlanHref },
        { label: "Evidence", href: "/start/evidence" },
        { label: "About", href: "/start/about" },
        { label: "Contact", href: "/start/contact" },
      ],
    },
    {
      label: "Live evidence",
      links: [
        { label: "Operations", href: "/data/operations" },
        { label: "Climate", href: "/start/climate" },
        { label: "Planning quality", href: "/data/planning-quality" },
        { label: "Resource use", href: "/start/resource-use" },
        { label: "Forecast", href: "/data/forecast/" },
        { label: "Planning archive", href: "/data/plans/" },
      ],
    },
    {
      label: "Greenhouse",
      links: [
        { label: "Greenhouse tour", href: "/greenhouse/" },
        { label: "Equipment", href: "/greenhouse/equipment" },
        { label: "Crops", href: "/greenhouse/crops/" },
        { label: "Zones", href: "/greenhouse/zones/" },
        { label: "Lighting", href: "/greenhouse/lighting" },
        { label: "Irrigation", href: "/greenhouse/irrigation" },
      ],
    },
    {
      label: "Reference",
      links: [
        { label: "Planning loop", href: "/reference/planning-loop" },
        { label: "Planner contract", href: "/reference/planner-contract" },
        { label: "AI tunables", href: "/reference/ai-tunables" },
        { label: "Lessons", href: "/reference/lessons" },
        { label: "Safety", href: "/reference/safety" },
        { label: "Architecture", href: "/reference/architecture" },
      ],
    },
  ];
}
