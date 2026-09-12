<?php

namespace App\Store;

trait Cacheable
{
    public const CACHE_TTL = 60;

    public function cache(): array
    {
        return [];
    }
}
