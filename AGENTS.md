# AGENTS.md

# Apanel Coding Agent Instructions

## 1. Project Overview

You are working on **Apanel v1**.

Apanel is an A-share stock technical indicator monitoring and alert SaaS
platform.

Core goal:

    Market Data
        ↓
    Indicator Calculation
        ↓
    State Recognition
        ↓
    Watch Table Display
        ↓
    Alert Rule
        ↓
    Notification

Apanel does not:

-   Predict stock prices
-   Provide buy/sell recommendations
-   Use AI trading decisions
-   Perform automatic trading

The product focuses on:

-   Technical indicator monitoring
-   Small changes detection
-   User-defined monitoring tables
-   Notification alerts

------------------------------------------------------------------------

# 2. Read Documents Before Coding

Before implementing features, read:

    Apanel_v1_PRD.md

    Apanel_v1_Architecture.md

    Apanel_v1_Database_Schema.md

    Apanel_v1_API_Design.md

    Apanel_v1_Frontend_Implementation.md

    Apanel_v1_Backend_Implementation.md

    Apanel_Market_Data_Service.md

    Apanel_v1_Design_System.md

    Apanel_v1_MVP_Development_Plan.md

These documents define product and technical decisions.

Do not override them without discussion.

------------------------------------------------------------------------

# 3. Development Philosophy

## Build Incrementally

Do not implement the entire product at once.

Follow:

    Sprint

    ↓

    Implementation

    ↓

    Test

    ↓

    Review

    ↓

    Next Sprint

------------------------------------------------------------------------

# 4. Architecture Rules

## 4.1 Backend

Technology:

-   Python
-   FastAPI
-   SQLAlchemy 2
-   PostgreSQL
-   Redis
-   Celery

Backend uses modular architecture.

Structure:

    backend

    ├── api

    ├── services

    ├── models

    ├── schemas

    ├── repositories

    ├── providers

    └── tasks

------------------------------------------------------------------------

## 4.2 Do Not Put Business Logic in API Layer

Wrong:

    API Controller

        calculate RSI

        send notification

Correct:

    API

    ↓

    Service

    ↓

    Domain Logic

------------------------------------------------------------------------

# 5. Provider Pattern

All external dependencies must be abstracted.

## Market Data

Never:

    Backend → TDX directly

Use:

    MarketDataProvider

    ↓

    TDXProvider

Future providers:

-   AKShare
-   Tonghuashun
-   Other providers

------------------------------------------------------------------------

## Notification

Never:

    Alert → Feishu API

Use:

    NotificationProvider

    ↓

    FeishuProvider

Future:

-   WeChat
-   Email
-   Telegram

------------------------------------------------------------------------

# 6. Database Rules

Use:

-   PostgreSQL
-   SQLAlchemy
-   Alembic migrations

Rules:

-   All user data must have user_id
-   Never trust user_id from frontend
-   Permission checks happen in backend

------------------------------------------------------------------------

# 7. Frontend Rules

Technology:

-   React
-   TypeScript
-   Next.js
-   Tailwind CSS
-   shadcn/ui
-   TanStack Table
-   TanStack Query

------------------------------------------------------------------------

## Frontend Principles

Frontend should:

-   Display data
-   Handle interaction
-   Manage UI state

Frontend should NOT:

-   Calculate RSI
-   Calculate MACD
-   Calculate BOLL
-   Decide indicator states

Those belong to backend.

------------------------------------------------------------------------

# 8. Indicator Rules

Indicators are plugin based.

Supported v1:

-   MA
-   Projected MA
-   RSI
-   KDJ
-   BOLL
-   MACD

Do not create separate tables for each indicator.

Use:

    IndicatorSnapshot

with extensible data.

------------------------------------------------------------------------

# 9. State Engine Rules

States are first-class objects.

Example:

    BOLL_WIDTH_NARROWING

    MA_CROSS_UP

    MACD_RED_BAR_SHRINKING

A state displayed in the table should be available for alerts.

Never duplicate state logic between:

-   frontend
-   alert system

------------------------------------------------------------------------

# 10. Alert Rules

Alert system uses Edge Trigger.

Example:

    RSI 69

    ↓

    RSI 71

    send notification

    ↓

    RSI 72

    no notification

Do not send repeated notifications while the state remains active.

------------------------------------------------------------------------

# 11. UI Rules

Apanel Web is a professional workspace.

Use:

-   Sidebar navigation
-   High information density
-   Dark theme
-   Clear state visualization

Avoid:

-   Marketing style pages
-   Excessive cards
-   Large empty spaces

------------------------------------------------------------------------

# 12. Component Rules

Reusable components should be created.

Important components:

    WatchTable

    IndicatorCell

    StateTag

    CompositeCell

    AlertCard

    Drawer

    Modal

------------------------------------------------------------------------

# 13. Code Quality Rules

Before submitting code:

Check:

-   Type safety
-   Error handling
-   Logging
-   Tests
-   Migration correctness

Avoid:

-   Hardcoded values
-   Duplicate logic
-   Temporary hacks

------------------------------------------------------------------------

# 14. Docker Rules

All services must run through:

    docker compose

Expected services:

    frontend

    backend

    market-data-service

    scheduler

    postgres

    redis

    nginx

------------------------------------------------------------------------

# 15. Commit Rules

Commits should be small and meaningful.

Example:

Good:

    feat: add watch table API

    fix: validate alert ownership

    refactor: extract market data provider

Bad:

    update
    fix
    changes

------------------------------------------------------------------------

# 16. When Requirements Are Unclear

Do not guess.

Instead:

1.  Check existing documents
2.  Identify conflict
3.  Ask for clarification

Preserve architecture consistency.

------------------------------------------------------------------------

# 17. First Development Task

When starting the project:

Do not build business features immediately.

First complete:

Sprint 0:

-   Repository structure
-   Docker Compose
-   Backend skeleton
-   Frontend skeleton
-   Database connection
-   Health check APIs

Verify:

    docker compose up

works successfully.

------------------------------------------------------------------------

# 18. Final Principle

Apanel is not a data display system.

It is:

    Indicators

    ↓

    States

    ↓

    User Attention

    ↓

    Notifications

Every implementation decision should support this goal.
