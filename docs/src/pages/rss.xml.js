import rss from '@astrojs/rss';

export async function GET(context) {
  const currentYear = new Date().getFullYear();
  const startYear = 2022;
  
  // Generate RSS items for news
  const items = [];
  
  // Add recent news items (you can expand this to fetch actual content)
  for (let year = currentYear; year >= startYear; year--) {
    for (let month = 12; month >= 1; month--) {
      const monthStr = month.toString().padStart(2, '0');
      
      // Add a sample item for each month (in real implementation, you'd fetch actual news)
      items.push({
        title: `Xinwen Lianbo ${year}-${monthStr}`,
        pubDate: new Date(year, month - 1, 1),
        description: `News archive for ${year}-${monthStr}`,
        link: `/Library/news/${year}/${monthStr}/`,
      });
      
      // Limit to recent items to keep RSS manageable
      if (items.length >= 50) break;
    }
    if (items.length >= 50) break;
  }

  return rss({
    title: 'Library Documentation',
    description: 'Updates from Library documentation and resources',
    site: context.site || 'https://dearealeo.github.io',
    items: items,
    customData: `<language>en-us</language>`,
  });
}
