# Polymarket Subgraph Backtesting Framework Design

## 1. Architecture Overview

The system is designed to be modular, separating data fetching, strategy logic, execution simulation, and reporting.

```mermaid
graph TD
    A[Data Loader] -->|Raw Trades| B[Data Processor]
    B -->|OHLCV / Ticks| C[Strategy Engine]
    D[Strategy Definition] --> C
    C -->|Signals| E[Execution Simulator]
    E -->|Trades & PnL| F[Performance Reporter]
    G[Local Cache (Parquet)] <--> A
```

## 2. Core Components

### A. Data Loader & Caching (`data_loader.py`)
*   **Responsibility**: Fetch historical trade data from The Graph and cache it locally.
*   **Key Features**:
    *   `fetch_market_history(market_id, start_ts, end_ts)`: Queries subgraph.
    *   **Caching**: Checks `data/raw/{market_id}.parquet` before querying API.
    *   **Normalization**: Converts raw subgraph JSON to a standard Pandas DataFrame (`timestamp`, `price`, `amount`, `side`).

### B. Data Processor (`processor.py`)
*   **Responsibility**: Clean and prepare data for strategies.
*   **Key Features**:
    *   **Resampling**: Converts tick data to fixed intervals (1min, 5min) if the strategy needs candles.
    *   **Fill Logic**: Implements "Forward Fill" for inactive periods (critical for prediction markets).
    *   **Feature Engineering**: Adds derived columns (e.g., `rolling_volatility`, `price_velocity`).

### C. Strategy Interface (`strategy.py`)
*   **Responsibility**: Define a standard interface that all strategies must implement.
*   **Base Class**:
    ```python
    class BaseStrategy:
        def on_data(self, current_data, portfolio):
            """
            Input: Current tick/candle, Current holdings
            Output: List of Orders (Buy/Sell, Amount, Price)
            """
            pass
    ```
*   **Example Strategies**:
    *   `MomentumStrategy`: Buy if price rises X% in Y minutes.
    *   `MeanReversionStrategy`: Buy if RSI < 30.

### D. Execution Simulator (`engine.py`)
*   **Responsibility**: Simulate trades based on strategy signals with realistic constraints.
*   **Key Features**:
    *   **Order Matching**: Only executes `BUY` if a future trade exists in data at/below price (or use "Last Traded Price" rule).
    *   **Friction Model**: Applies `slippage` (e.g., 1%) and `fees` (e.g., 2% on profit).
    *   **Position Management**: Tracks cash, shares (YES/NO tokens), and exposure.

### E. Performance Reporter (`reporter.py`)
*   **Responsibility**: Calculate metrics and visualize results.
*   **Metrics**: Total Return, Sharpe Ratio, Max Drawdown, Win Rate, Average Trade Duration.
*   **Visualization**: Plot Price Curve overlaid with Buy/Sell markers and Equity Curve.

## 3. Technology Stack
*   **Language**: Python 3.10+
*   **Data Analysis**: Pandas, NumPy
*   **Storage**: PyArrow (Parquet)
*   **Visualization**: Matplotlib / Plotly
*   **API Client**: `requests` / `gql`

## 4. Next Steps
1.  Implement `data_loader.py` to fetch from The Graph.
2.  Create a simple `MeanReversionStrategy` as a proof-of-concept.
3.  Run a backtest on a single NHL market.
