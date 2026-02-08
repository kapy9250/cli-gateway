// Quick ROI median calculation
const fs = require('fs');
const path = require('path');

const DATA_DIR = '/workspace/workspaces/connie/polymarket-data';
const FILLS_DIR = path.join(DATA_DIR, 'fills');
const index = JSON.parse(fs.readFileSync(path.join(DATA_DIR, 'moneyline-index.json'), 'utf8'));
const indexMap = new Map(index.map(g => [g.slug, g]));

function getPrice(fill) {
    const maker = Number(fill.makerAmountFilled);
    const taker = Number(fill.takerAmountFilled);
    if (fill.makerAssetId === '0') return maker / taker;
    if (fill.takerAssetId === '0') return taker / maker;
    return null;
}

const allResults = [];
const files = fs.readdirSync(FILLS_DIR).filter(f => f.endsWith('-fills.json')).slice(0, 200); // limit for speed

for (const file of files) {
    const slug = file.replace('-fills.json', '');
    const gameInfo = indexMap.get(slug);
    if (!gameInfo) continue;
    
    let fillsData;
    try {
        fillsData = JSON.parse(fs.readFileSync(path.join(FILLS_DIR, file), 'utf8'));
    } catch { continue; }
    if (!fillsData?.fills?.length) continue;
    
    const startTime = new Date(gameInfo.startTime).getTime();
    const pregameStart = startTime - 24 * 60 * 60 * 1000;
    const gameEnd = startTime + 180 * 60 * 1000;
    
    for (const outcome of gameInfo.outcomes) {
        const outcomeFills = fillsData.fills.filter(f => f.outcome === outcome);
        
        const pregameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= pregameStart && ts < startTime;
        }).map(f => {
            const price = getPrice(f);
            return price && price > 0 && price < 1 ? { timestamp: Number(f.timestamp) * 1000, price } : null;
        }).filter(Boolean);
        
        const ingameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= startTime && ts < gameEnd;
        }).map(f => {
            const price = getPrice(f);
            const ts = Number(f.timestamp) * 1000;
            return price && price > 0 && price < 1 ? { ts, price, min: (ts - startTime) / 60000 } : null;
        }).filter(Boolean);
        
        if (pregameFills.length === 0) continue;
        
        const entryFill = pregameFills.find(f => f.price < 0.40);
        if (!entryFill) continue;
        
        const entryPrice = entryFill.price * 1.005;
        const density = pregameFills.length / 24;
        
        let exitPrice = null, exitType = 'force';
        for (const fill of ingameFills) {
            if (fill.price >= entryPrice * 2.0 / 0.995) {
                exitPrice = fill.price * 0.995;
                exitType = 'target';
                break;
            }
            if (fill.min >= 165) {
                exitPrice = fill.price * 0.995;
                exitType = '165min';
                break;
            }
        }
        if (!exitPrice && ingameFills.length > 0) {
            exitPrice = ingameFills[ingameFills.length - 1].price * 0.995;
        } else if (!exitPrice) {
            exitPrice = entryPrice * 0.5;
        }
        
        const roi = (exitPrice / entryPrice) - 1 - 0.001;
        allResults.push({ roi, density });
    }
}

// Bucket by density quartiles
const sorted = [...allResults].sort((a, b) => a.density - b.density);
const n = sorted.length;
const q1d = sorted[Math.floor(n * 0.25)].density;
const med = sorted[Math.floor(n * 0.5)].density;
const q3d = sorted[Math.floor(n * 0.75)].density;

const buckets = [
    { name: 'Q1', data: allResults.filter(r => r.density < q1d) },
    { name: 'Q2', data: allResults.filter(r => r.density >= q1d && r.density < med) },
    { name: 'Q3', data: allResults.filter(r => r.density >= med && r.density < q3d) },
    { name: 'Q4', data: allResults.filter(r => r.density >= q3d) }
];

console.log('Total:', allResults.length);
console.log('Density thresholds: Q1<' + q1d.toFixed(1) + ', Q2<' + med.toFixed(1) + ', Q3<' + q3d.toFixed(1));
console.log('\\n| Quartile | N | Mean ROI | Median ROI | Min | Max |');
console.log('|----------|---|----------|------------|-----|-----|');

for (const b of buckets) {
    const rois = b.data.map(r => r.roi * 100).sort((a, b) => a - b);
    const mean = rois.reduce((s, r) => s + r, 0) / rois.length;
    const median = rois[Math.floor(rois.length / 2)];
    const min = rois[0];
    const max = rois[rois.length - 1];
    console.log(`| ${b.name} | ${rois.length} | ${mean.toFixed(1)}% | ${median.toFixed(1)}% | ${min.toFixed(0)}% | ${max.toFixed(0)}% |`);
}

// Show Q1 distribution
const q1Rois = buckets[0].data.map(r => r.roi * 100).sort((a, b) => b - a);
console.log('\\nQ1 ROI distribution:');
console.log('Top 5:', q1Rois.slice(0, 5).map(r => r.toFixed(0) + '%').join(', '));
console.log('Bottom 5:', q1Rois.slice(-5).map(r => r.toFixed(0) + '%').join(', '));
