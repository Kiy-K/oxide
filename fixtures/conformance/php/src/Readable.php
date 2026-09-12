<?php

namespace App\Store;

interface Readable extends \Countable
{
    public function read(string $key): string;
}
