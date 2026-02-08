/**
 * Entry Price Tier Analysis
 * Analyze price patterns for three entry price tiers:
 * - Tier A: entry < 0.30
 * - Tier B: 0.30 ≤ entry < 0.35
 * - Tier C: 0.35 ≤ entry < 0.40
 */

const fs = require('fs');
const path = require('path');

const DATA_DIR = '/workspace/workspaces/connie/polymarket-data';
const FILLS_DIR = path.join(DATA_DIR, 'fills');
const INDEX_FILE = path.join(DATA_DIR, 'moneyline-index.json');

const PREGAME_WINDOW_HOURS = 24;
const GAME_DURATION_MINUTES = 180;

const index = JSON.parse(fs.readFileSync(INDEX_FILE, 'utf8'));
const indexMap = new Map(index.map(g => [g.slug, g]));

function getPrice(fill) {
    const maker = Number(fill.makerAmountFilled);
    const taker = Number(fill.takerAmountFilled);
    if (fill.makerAssetId === '0') return maker / taker;
    if (fill.takerAssetId === '0') return taker / maker;
    return null;
}

const results = {
    'A (<0.30)': [],
    'B (0.30-0.35)': [],
    'C (0.35-0.40)': []
};

const files = fs.readdirSync(FILLS_DIR).filter(f => f.endsWith('-fills.json'));

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
    const pregameStart = startTime - PREGAME_WINDOW_HOURS * 60 * 60 * 1000;
    const gameEnd = startTime + GAME_DURATION_MINUTES * 60 * 1000;
    
    for (const outcome of gameInfo.outcomes) {
        const outcomeFills = fillsData.fills.filter(f => f.outcome === outcome);
        
        // Pregame fills
        const pregameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= pregameStart && ts < startTime;
        }).map(f => {
            const price = getPrice(f);
            return price && price > 0 && price < 1 ? { ts: Number(f.timestamp) * 1000, price } : null;
        }).filter(Boolean);
        
        // Ingame fills
        const ingameFills = outcomeFills.filter(f => {
            const ts = Number(f.timestamp) * 1000;
            return ts >= startTime && ts < gameEnd;
        }).map(f => {
            const price = getPrice(f);
            const ts = Number(f.timestamp) * 1000;
            return price && price > 0 && price < 1 ? { 
                ts, 
                price, 
                minSinceStart: (ts - startTime) / 60000 
            } : null;
        }).filter(Boolean);
        
        if (pregameFills.length === 0 || ingameFills.length === 0) continue;
        
        // Find first trigger under 0.40
        const entryFill = pregameFills.find(f => f.price < 0.40);
        if (!entryFill) continue;
        
        const entryPrice = entryFill.price;
        
        // Determine tier
        let tier;
        if (entryPrice < 0.30) tier = 'A (<0.30)';
        else if (entryPrice < 0.35) tier = 'B (0.30-0.35)';
        else tier = 'C (0.35-0.40)';
        
        // Analyze price trajectory
        let maxPrice = entryPrice;
        let maxPriceTime = 0;
        let priceAt30min = null;
        let priceAt60min = null;
        let priceAt120min = null;
        let priceAt165min = null;
        
        // Track price at key intervals
        for (const fill of ingameFills) {
            if (fill.price > maxPrice) {
                maxPrice = fill.price;
                maxPriceTime = fill.minSinceStart;
            }
            if (priceAt30min === null && fill.minSinceStart >= 30) priceAt30min = fill.price;
            if (priceAt60min === null && fill.minSinceStart >= 60) priceAt60min = fill.price;
            if (priceAt120min === null && fill.minSinceStart >= 120) priceAt120min = fill.price;
            if (priceAt165min === null && fill.minSinceStart >= 165) priceAt165min = fill.price;
        }
        
        // Calculate metrics
        const maxGain = (maxPrice / entryPrice - 1) * 100;
        const hit2x = maxPrice >= entryPrice * 2;
        const hit1_5x = maxPrice >= entryPrice * 1.5;
        
        // Time bucket for max price
        let maxPriceBucket;
        if (maxPriceTime <= 30) maxPriceBucket = '0-30min';
        else if (maxPriceTime <= 60) maxPriceBucket = '30-60min';
        else if (maxPriceTime <= 120) maxPriceBucket = '60-120min';
        else if (maxPriceTime <= 150) maxPriceBucket = '120-150min';
        else maxPriceBucket = '150-180min';
        
        results[tier].push({
            slug,
            outcome,
            entryPrice,
            maxPrice,
            maxPriceTime,
            maxPriceBucket,
            maxGain,
            hit2x,
            hit1_5x,
            priceAt30min,
            priceAt60min,
            priceAt120min,
            priceAt165min
        });
    }
}

// Analyze each tier
console.log('=' .repeat(70));
console.log('ENTRY PRICE TIER ANALYSIS');
console.log('='.repeat(70));

for (const [tier, data] of Object.entries(results)) {
    console.log(`\n${'─'.repeat(70)}`);
    console.log(`TIER ${tier} — ${data.length} samples`);
    console.log('─'.repeat(70));
    
    if (data.length === 0) {
        console.log('No samples in this tier');
        continue;
    }
    
    // Basic stats
    const avgEntryPrice = data.reduce((s, d) => s + d.entryPrice, 0) / data.length;
    const avgMaxGain = data.reduce((s, d) => s + d.maxGain, 0) / data.length;
    const medianMaxGain = data.map(d => d.maxGain).sort((a, b) => a - b)[Math.floor(data.length / 2)];
    const hit2xRate = data.filter(d => d.hit2x).length / data.length * 100;
    const hit1_5xRate = data.filter(d => d.hit1_5x).length / data.length * 100;
    
    console.log('\n📊 Basic Stats:');
    console.log(`  Avg Entry Price: ${avgEntryPrice.toFixed(4)}`);
    console.log(`  Avg Max Gain: ${avgMaxGain.toFixed(1)}%`);
    console.log(`  Median Max Gain: ${medianMaxGain.toFixed(1)}%`);
    console.log(`  Hit 2x Rate: ${hit2xRate.toFixed(1)}%`);
    console.log(`  Hit 1.5x Rate: ${hit1_5xRate.toFixed(1)}%`);
    
    // Max price timing distribution
    const timeBuckets = {};
    for (const d of data) {
        timeBuckets[d.maxPriceBucket] = (timeBuckets[d.maxPriceBucket] || 0) + 1;
    }
    
    console.log('\n⏱️ Max Price Timing Distribution:');
    for (const bucket of ['0-30min', '30-60min', '60-120min', '120-150min', '150-180min']) {
        const count = timeBuckets[bucket] || 0;
        const pct = (count / data.length * 100).toFixed(1);
        const bar = '█'.repeat(Math.round(pct / 2));
        console.log(`  ${bucket.padEnd(12)}: ${String(count).padStart(3)} (${pct.padStart(5)}%) ${bar}`);
    }
    
    // Avg max price time
    const avgMaxTime = data.reduce((s, d) => s + d.maxPriceTime, 0) / data.length;
    console.log(`\n  Average Max Price Time: ${avgMaxTime.toFixed(1)} minutes`);
    
    // Price trajectory at key intervals (for samples that have data)
    const validAt30 = data.filter(d => d.priceAt30min !== null);
    const validAt60 = data.filter(d => d.priceAt60min !== null);
    const validAt120 = data.filter(d => d.priceAt120min !== null);
    const validAt165 = data.filter(d => d.priceAt165min !== null);
    
    console.log('\n📈 Price Trajectory (avg % change from entry):');
    if (validAt30.length > 0) {
        const avgChange30 = validAt30.reduce((s, d) => s + (d.priceAt30min / d.entryPrice - 1) * 100, 0) / validAt30.length;
        console.log(`  At 30min:  ${avgChange30 > 0 ? '+' : ''}${avgChange30.toFixed(1)}% (n=${validAt30.length})`);
    }
    if (validAt60.length > 0) {
        const avgChange60 = validAt60.reduce((s, d) => s + (d.priceAt60min / d.entryPrice - 1) * 100, 0) / validAt60.length;
        console.log(`  At 60min:  ${avgChange60 > 0 ? '+' : ''}${avgChange60.toFixed(1)}% (n=${validAt60.length})`);
    }
    if (validAt120.length > 0) {
        const avgChange120 = validAt120.reduce((s, d) => s + (d.priceAt120min / d.entryPrice - 1) * 100, 0) / validAt120.length;
        console.log(`  At 120min: ${avgChange120 > 0 ? '+' : ''}${avgChange120.toFixed(1)}% (n=${validAt120.length})`);
    }
    if (validAt165.length > 0) {
        const avgChange165 = validAt165.reduce((s, d) => s + (d.priceAt165min / d.entryPrice - 1) * 100, 0) / validAt165.length;
        console.log(`  At 165min: ${avgChange165 > 0 ? '+' : ''}${avgChange165.toFixed(1)}% (n=${validAt165.length})`);
    }
    
    // Top 5 and bottom 5 max gains
    const sortedByGain = [...data].sort((a, b) => b.maxGain - a.maxGain);
    console.log('\n🔝 Top 5 Max Gains:');
    sortedByGain.slice(0, 5).forEach((d, i) => {
        console.log(`  ${i + 1}. ${d.maxGain.toFixed(0)}% (entry: ${d.entryPrice.toFixed(4)}, peak at ${d.maxPriceTime.toFixed(0)}min)`);
    });
    
    console.log('\n🔻 Bottom 5 Max Gains:');
    sortedByGain.slice(-5).forEach((d, i) => {
        console.log(`  ${sortedByGain.length - 4 + i}. ${d.maxGain.toFixed(0)}% (entry: ${d.entryPrice.toFixed(4)}, peak at ${d.maxPriceTime.toFixed(0)}min)`);
    });
}

// Cross-tier comparison
console.log('\n' + '='.repeat(70));
console.log('CROSS-TIER COMPARISON');
console.log('='.repeat(70));

console.log('\n| Tier | Samples | Avg MaxGain | Median MaxGain | Hit 2x | Hit 1.5x | Avg Peak Time |');
console.log('|------|---------|-------------|----------------|--------|----------|---------------|');

for (const [tier, data] of Object.entries(results)) {
    if (data.length === 0) continue;
    const avgGain = data.reduce((s, d) => s + d.maxGain, 0) / data.length;
    const medGain = data.map(d => d.maxGain).sort((a, b) => a - b)[Math.floor(data.length / 2)];
    const hit2x = data.filter(d => d.hit2x).length / data.length * 100;
    const hit1_5x = data.filter(d => d.hit1_5x).length / data.length * 100;
    const avgTime = data.reduce((s, d) => s + d.maxPriceTime, 0) / data.length;
    
    console.log(`| ${tier.padEnd(12)} | ${String(data.length).padStart(7)} | ${avgGain.toFixed(1).padStart(11)}% | ${medGain.toFixed(1).padStart(14)}% | ${hit2x.toFixed(1).padStart(6)}% | ${hit1_5x.toFixed(1).padStart(8)}% | ${avgTime.toFixed(0).padStart(13)}min |`);
}

// Strategy implications
console.log('\n' + '='.repeat(70));
console.log('STRATEGY IMPLICATIONS');
console.log('='.repeat(70));

const tierA = results['A (<0.30)'];
const tierB = results['B (0.30-0.35)'];
const tierC = results['C (0.35-0.40)'];

if (tierA.length > 0 && tierB.length > 0 && tierC.length > 0) {
    const aHit2x = tierA.filter(d => d.hit2x).length / tierA.length * 100;
    const bHit2x = tierB.filter(d => d.hit2x).length / tierB.length * 100;
    const cHit2x = tierC.filter(d => d.hit2x).length / tierC.length * 100;
    
    console.log('\n💡 KEY FINDINGS:');
    
    if (cHit2x > bHit2x) {
        console.log(`\n1. TIER C (0.35-0.40) has HIGHER 2x hit rate than TIER B (0.30-0.35)`);
        console.log(`   → ${cHit2x.toFixed(1)}% vs ${bHit2x.toFixed(1)}%`);
        console.log(`   → Confirms: 0.30-0.35 is a "danger zone" to avoid`);
    }
    
    if (aHit2x > bHit2x) {
        console.log(`\n2. TIER A (<0.30) has higher potential but requires more analysis`);
        console.log(`   → ${tierA.length} samples, ${aHit2x.toFixed(1)}% hit 2x`);
    }
    
    // Late peak analysis
    const aLatePeak = tierA.filter(d => d.maxPriceTime >= 120).length / tierA.length * 100;
    const bLatePeak = tierB.filter(d => d.maxPriceTime >= 120).length / tierB.length * 100;
    const cLatePeak = tierC.filter(d => d.maxPriceTime >= 120).length / tierC.length * 100;
    
    console.log(`\n3. LATE PEAK (≥120min) FREQUENCY:`);
    console.log(`   Tier A: ${aLatePeak.toFixed(1)}%`);
    console.log(`   Tier B: ${bLatePeak.toFixed(1)}%`);
    console.log(`   Tier C: ${cLatePeak.toFixed(1)}%`);
}

// Save results
fs.writeFileSync('/workspace/workspaces/manny/entry-price-analysis-results.json', JSON.stringify(results, null, 2));
console.log('\n✅ Detailed results saved to entry-price-analysis-results.json');
